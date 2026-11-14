"""Dialogue in the web interface: history per browser, follow-ups rewritten, scripts unaffected."""
from contextlib import closing
import http.client
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'eval')]
from app import history, rag  # noqa: E402
from synthetic_docs import SUPPORT_PAGES, write_pdf  # noqa: E402


class FakeModel:
    def __init__(self, reply):
        self.reply, self.seen = reply, []

    def complete(self, messages, schema=None, max_tokens=None):
        self.seen.append(messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        return json.dumps({'question': self.reply}, ensure_ascii=False)


TURNS = [{'question': 'Кто рассматривает заявки на закупку?', 'answer_text': 'Заявки рассматривает отдел закупок в течение 7 рабочих дней.'}]


class CondenseTest(unittest.TestCase):
    def test_follow_up_is_rewritten(self):
        model = FakeModel('Где кабинет отдела закупок?')
        self.assertEqual(rag.condense(model, TURNS, 'А где их кабинет?'), 'Где кабинет отдела закупок?')
        self.assertIn('Кто рассматривает заявки на закупку?', model.seen[0][1]['content'])

    def test_without_history_or_model_the_question_stays(self):
        self.assertEqual(rag.condense(FakeModel('другое'), [], 'А где их кабинет?'), 'А где их кабинет?')
        self.assertEqual(rag.condense(None, TURNS, 'А где их кабинет?'), 'А где их кабинет?')

    def test_number_from_an_answer_is_not_carried_over(self):
        # «7» comes from the previous answer, not from any question: an unchecked value.
        self.assertEqual(rag.condense(FakeModel('Отдел закупок рассматривает заявку 7 дней — почему?'), TURNS, 'А почему так долго?'),
                         'А почему так долго?')

    def test_failure_keeps_the_question(self):
        self.assertEqual(rag.condense(FakeModel(OSError('down')), TURNS, 'А где их кабинет?'), 'А где их кабинет?')


class HistoryTest(unittest.TestCase):
    def test_turns_are_kept_per_conversation_and_cleared(self):
        db = sqlite3.connect(':memory:'); db.row_factory = sqlite3.Row
        a, b = history.new_id(), history.new_id()
        history.add(db, a, 'q1', 'q1', 'a1', '<p>a1</p>')
        history.add(db, a, 'q2', 'Q2', 'a2', '<p>a2</p>')
        history.add(db, b, 'other', 'other', 'x', '<p>x</p>')
        self.assertEqual([t['question'] for t in history.turns(db, a)], ['q1', 'q2'])
        history.clear(db, a)
        self.assertEqual(history.turns(db, a), [])
        self.assertEqual(len(history.turns(db, b)), 1)

    def test_cookie_is_validated(self):
        good = history.new_id()
        self.assertEqual(history.from_cookie(f'x=1; {history.COOKIE}={good}'), good)
        self.assertIsNone(history.from_cookie(f'{history.COOKIE}=../../etc'))

    def test_plain_answer_has_no_service_lines(self):
        text = history.plain('<h2>Ответ</h2><ol><li>Кабинет 310. <a href="/fragment?id=x">x</a></li></ol><p>Источник: phones.xlsx.</p>')
        self.assertEqual(text, 'Кабинет 310.')


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); return s.getsockname()[1]


class ChatWebTest(unittest.TestCase):
    def request(self, method, path, body=None, cookie=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=60)
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        if cookie: headers['Cookie'] = cookie
        conn.request(method, path, body=urlencode(body).encode() if body else None, headers=headers)
        r = conn.getresponse(); data = r.read().decode('utf-8'); conn.close()
        return r.status, dict(r.getheaders()), data

    def test_dialogue_is_kept_and_scripts_are_unaffected(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp); (root / 'originals').mkdir()
            write_pdf(root / 'originals' / 'support.pdf', SUPPORT_PAGES)
            self.port = free_port()
            env = dict(os.environ, NAVIGATOR_DB=str(root / 'db.sqlite3'), NAVIGATOR_IMPORT_DIR=str(root / 'originals'),
                       HOST='127.0.0.1', PORT=str(self.port))
            for key in ('NAVIGATOR_MODEL', 'NAVIGATOR_RETRIEVAL', 'NAVIGATOR_PIPELINE', 'NAVIGATOR_LLM_URL', 'NAVIGATOR_HISTORY'):
                env.pop(key, None)
            server = subprocess.Popen([sys.executable, '-m', 'app'], cwd=ROOT, env=env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(300):
                    try:
                        status, headers, _ = self.request('GET', '/'); break
                    except OSError:
                        time.sleep(.1)
                cookie = headers['Set-Cookie'].split(';')[0]
                self.assertTrue(cookie.startswith(history.COOKIE + '='))
                # A script without the cookie: the single answer page, nothing kept.
                status, _, page = self.request('POST', '/ask', {'question': 'Не работает принтер, куда обратиться?'})
                self.assertEqual(status, 200)
                self.assertIn('</form>', page)
                # The browser: the turn is kept and shown with its answer.
                status, headers, _ = self.request('POST', '/ask', {'question': 'Не работает принтер, куда обратиться?'}, cookie)
                self.assertEqual(status, 303)
                _, _, page = self.request('GET', '/', cookie=cookie)
                self.assertIn('class="turn"', page)
                self.assertIn('Не работает принтер, куда обратиться?', page)
                self.assertIn('portal.example.invalid/support', page)
                with closing(sqlite3.connect(root / 'db.sqlite3')) as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM chat_turns').fetchone()[0], 1)
                # Another browser does not see it; «clear» empties this one.
                _, headers, page = self.request('GET', '/')
                self.assertNotIn('class="turn"', page)
                self.request('POST', '/clear', {}, cookie)
                _, _, page = self.request('GET', '/', cookie=cookie)
                self.assertNotIn('class="turn"', page)
            finally:
                server.terminate(); server.wait(timeout=10)


if __name__ == '__main__':
    unittest.main()
