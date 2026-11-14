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
from app.__main__ import decorate, imported_documents, ref_label, render_documents  # noqa: E402
from synthetic_docs import SUPPORT_PAGES, write_pdf  # noqa: E402


class FakeModel:
    def __init__(self, reply):
        self.reply, self.seen = reply, []

    def complete(self, messages, schema=None, max_tokens=None):
        self.seen.append(messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        assert schema is False, 'the rewriting asks for plain text'
        return '«' + self.reply + '»\n'


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


class PageTest(unittest.TestCase):
    def test_locator_labels(self):
        self.assertEqual(ref_label('F@v#page/2/text-layer'), 'стр. 2')
        self.assertEqual(ref_label('F@v#page/2/text-layer', 'Раздел', '3.2. Срок обработки'), 'стр. 2, п. 3.2')
        self.assertEqual(ref_label('F@v#body/3/paragraph', 'Текст без номера'), 'абзац 3')
        self.assertEqual(ref_label('F@v#body/3/paragraph', '3.1. Заявку подписывает'), 'п. 3.1')
        self.assertEqual(ref_label('F@v#body/7/paragraph', '4. Отпуск'), 'п. 4')
        self.assertEqual(ref_label('F@v#sheet/Справочник/cell/C5'), 'лист «Справочник», строка 5')
        self.assertEqual(ref_label('F@v#body/3/table/row/2'), 'таблица, строка 2')
        self.assertEqual(ref_label('SYN-SUPPORT-v1-p3'), 'SYN-SUPPORT-v1-p3')

    def test_citation_keeps_href_and_shows_a_label(self):
        link = '<li>Кабинет 310. <a href="/fragment?id=F%40v%23page%2F2%2Ftext-layer&amp;at=1-5">F@v#page/2/text-layer</a></li>'
        out = decorate(link)
        self.assertIn('<a href="/fragment?id=F%40v%23page%2F2%2Ftext-layer&amp;at=1-5" class="ref"', out)
        self.assertIn('title="F@v#page/2/text-layer">стр. 2</a>', out)
        self.assertIn('<p class="n-src">Источник:', decorate('<p>Источник: x.</p>'))

    def test_cells_of_one_row_are_one_label_and_documents_are_named(self):
        db = sqlite3.connect(':memory:'); db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE imports (source_id TEXT, version TEXT, filename TEXT)')
        db.execute('CREATE TABLE import_fragments (id TEXT, source_id TEXT, version TEXT, locator TEXT, text TEXT, metadata TEXT)')
        db.executemany('INSERT INTO imports VALUES (?,?,?)', [('S1', 'v1', 'Справочник телефонов.xlsx'), ('S2', 'v2', 'procurement.pdf')])
        rows = [('S1@v1#sheet/Справочник/cell/A5', 'S1', 'v1', 'sheet/Справочник/cell/A5', 'Справочник A5 Отдел кадров'),
                ('S1@v1#sheet/Справочник/cell/C5', 'S1', 'v1', 'sheet/Справочник/cell/C5', 'Справочник C5 310'),
                ('S2@v2#page/1/text-layer', 'S2', 'v2', 'page/1/text-layer', 'ПОРЯДОК закупок малого объёма (учебный)\n1.1. Текст')]
        db.executemany('INSERT INTO import_fragments VALUES (?,?,?,?,?,"{}")', rows)
        a = lambda fid: '<a href="/fragment?id=' + fid + '">' + fid + '</a>'
        out = decorate('<ol><li>Отдел кадров, кабинет 310. ' + a(rows[0][0]) + ' ' + a(rows[1][0]) + '</li>'
                       '<li>Закупки. ' + a(rows[2][0]) + '</li></ol>', db)
        self.assertEqual(out.count('<a href='), 3)                     # every link stays
        self.assertIn('>Справочник телефонов, лист «Справочник», строка 5</a>', out)
        self.assertEqual(out.count('class="ref same"'), 1)              # the second cell of the row is hidden
        self.assertIn('>Порядок закупок малого объёма, стр. 1</a>', out)

    def test_documents_of_an_empty_base(self):
        db = sqlite3.connect(':memory:'); db.row_factory = sqlite3.Row
        self.assertEqual(imported_documents(db), [])
        self.assertIn('Документы ещё не загружены', render_documents([]))


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
                self.assertIn('href="/documents"', page)
                with closing(sqlite3.connect(root / 'db.sqlite3')) as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM chat_turns').fetchone()[0], 1)
                # Another browser does not see it; «clear» empties this one.
                _, headers, page = self.request('GET', '/')
                self.assertNotIn('class="turn"', page)
                self.request('POST', '/clear', {}, cookie)
                _, _, page = self.request('GET', '/', cookie=cookie)
                self.assertNotIn('class="turn"', page)
                # The list of searchable documents links each one to its first fragment.
                status, _, page = self.request('GET', '/documents')
                self.assertEqual(status, 200)
                self.assertIn('Документы в базе', page)
                self.assertIn('support.pdf', page)
                self.assertIn('href="/document?source=', page)     # the whole document, not its first fragment
            finally:
                server.terminate(); server.wait(timeout=10)


class SourceDocumentTest(unittest.TestCase):
    """The original documents: the whole text in reading order and the file as imported."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(cls.tmp.name)
        cls.pdf = write_pdf(root / 'support.pdf', SUPPORT_PAGES)
        from app.importer import batch
        cls.db_path = root / 'db.sqlite3'
        batch(str(cls.db_path), [str(cls.pdf)])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def db(self):
        db = sqlite3.connect(self.db_path); db.row_factory = sqlite3.Row
        return db

    def test_answer_order_answer_quotes_sources(self):
        from app.__main__ import render_rag
        with closing(self.db()) as db:
            from app import chunking
            chunking.ensure(db)
            chunk = dict(db.execute("SELECT * FROM chunks WHERE level=2 AND text LIKE '%portal.example.invalid%'").fetchone())
            chunk['meta'] = json.loads(chunk['meta'] or '{}')
            items = [{'text': 'Обращение подаётся через портал.', 'chunks': [chunk]},
                     {'text': 'В обращении указывают инвентарный номер.', 'chunks': [chunk]}]
            result = {'status': 'answer', 'short_answer': '', 'items': items,
                      'dropped': [], 'missing': [], 'headers': {(chunk['source_id'], chunk['version']): 'Регламент · support.pdf'}}
            html_ = render_rag(db, result, None, 'Куда подать обращение?')
            steps = render_rag(db, result, None, 'Как подать обращение?')
        # Not a numbered list unless the question asks what to do, step by step.
        self.assertNotIn('<ol', html_[:html_.index('class="quotes"')])
        self.assertIn('<p>Обращение подаётся через портал.', html_)
        self.assertIn('<ol class="steps"><li>Обращение подаётся через портал.', steps)
        answer, quotes, sources = html_.index('Ответ</h2>'), html_.index('class="quotes"'), html_.index('<h2>Источники')
        self.assertLess(answer, quotes)
        self.assertLess(quotes, sources)
        self.assertIn('/document?source=', html_)
        # Quotes are blocks, not statements of the answer: no <p>/<li> inside them.
        block = html_[quotes:sources]
        self.assertNotIn('<li>', block); self.assertNotIn('<p>', block)

    def test_document_page_and_original_file(self):
        import threading
        from http.server import HTTPServer
        import app.__main__ as web
        web.DB = str(self.db_path)
        server = HTTPServer(('127.0.0.1', 0), web.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        with closing(self.db()) as db:
            row = db.execute('SELECT source_id, version FROM imports').fetchone()
            first = db.execute('SELECT id FROM import_fragments ORDER BY rowid LIMIT 1').fetchone()[0]
            count = db.execute('SELECT COUNT(*) FROM import_fragments').fetchone()[0]
        from urllib.parse import quote
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=30)
        conn.request('GET', f'/document?source={quote(row[0])}&version={quote(row[1])}&at={quote(first)}')
        r = conn.getresponse(); page = r.read().decode('utf-8')
        self.assertEqual(r.status, 200)
        self.assertEqual(page.count('<strong title='), count)          # every fragment, in order
        self.assertIn('id="selected"', page)
        self.assertIn('/original?source=', page)
        conn.request('GET', f'/original?source={quote(row[0])}&version={quote(row[1])}')
        r = conn.getresponse(); data = r.read()
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader('Content-Type'), 'application/pdf')
        self.assertIn("filename*=UTF-8''support.pdf", r.getheader('Content-Disposition'))
        self.assertEqual(data, Path(self.pdf).read_bytes())
        conn.request('GET', '/original?source=nope&version=nope')
        r = conn.getresponse(); r.read()
        self.assertEqual(r.status, 404)
        conn.request('GET', '/documents')
        r = conn.getresponse(); page = r.read().decode('utf-8')
        self.assertIn('/document?source=', page)
        self.assertIn('исходный файл (PDF', page)


class RelationTest(unittest.TestCase):
    """How a new question relates to the session: decided by code, before any model."""
    EARLIER = [{'question': 'Кто рассматривает заявки на закупку?', 'standalone': 'Кто рассматривает заявки на закупку?',
                'answer_text': 'Отдел закупок.'}]

    def test_reference_or_missing_subject_is_a_follow_up(self):
        for q in ('А где их кабинет?', 'А раньше?', 'Сколько дней?', 'Тот же вопрос про бухгалтерию', 'Почему так долго?'):
            with self.subTest(q=q):
                self.assertEqual(history.relation(q, self.EARLIER), 'follow')

    def test_own_subject_is_a_new_topic(self):
        for q in ('Где взять бланк заявления на пропуск?', 'Какой телефон у отдела кадров?', 'Кто подписывает заявку?'):
            with self.subTest(q=q):
                self.assertEqual(history.relation(q, self.EARLIER), 'new')

    def test_first_question_is_new(self):
        self.assertEqual(history.relation('А где их кабинет?', []), 'new')

    def test_unchanged_rewrite_gets_the_previous_question(self):
        model = FakeModel('А в пятницу?')
        turns = [{'question': 'До скольки работает служба поддержки?', 'standalone': 'До скольки работает служба поддержки?',
                  'answer_text': 'С 8:30 до 17:30.'}]
        self.assertEqual(rag.condense(model, turns, 'А в пятницу?'), 'До скольки работает служба поддержки? А в пятницу?')

    def test_chain_uses_questions_as_understood(self):
        model = FakeModel('Какой телефон отдела закупок?')
        turns = self.EARLIER + [{'question': 'А где их кабинет?', 'standalone': 'Где кабинет отдела закупок?', 'answer_text': 'Кабинет 310.'}]
        rag.condense(model, turns, 'А телефон?')
        self.assertIn('Где кабинет отдела закупок?', model.seen[0][1]['content'])

    def test_now_stops_the_date(self):
        self.assertTrue(history.NOW.search('А сейчас?'))
        self.assertFalse(history.NOW.search('А раньше?'))

    def test_session_window(self):
        db = sqlite3.connect(':memory:'); db.row_factory = sqlite3.Row
        c = history.new_id()
        history.add(db, c, 'q1', 'q1', 'a1', '<p>a1</p>')
        db.execute("UPDATE chat_turns SET created='2000-01-01 00:00:00'")
        history.add(db, c, 'q2', 'q2', 'a2', '<p>a2</p>')
        self.assertEqual([t['question'] for t in history.session(db, c)], ['q2'])


class UnderstoodLineTest(unittest.TestCase):
    """The line under a follow-up: meaning, the earlier question (a link), the date, «Это новый вопрос»."""
    FIRST = {'n': 1, 'question': 'До скольки работает служба поддержки?', 'standalone': 'До скольки работает служба поддержки?'}

    def line(self, turn):
        from app.__main__ import understood
        return understood(dict({'n': 2, 'asked_on': None}, **turn), {1: self.FIRST})

    def test_rewritten_follow_up(self):
        html_ = self.line({'question': 'А где их кабинет?', 'standalone': 'Где кабинет службы поддержки?', 'relation': 'follow', 'ref': 1})
        self.assertIn('Понял вопрос как: «Где кабинет службы поддержки?» — уточнение к <a href="#t1">вопросу 1</a>', html_)
        self.assertIn('name="fresh" value="1"', html_)

    def test_joined_follow_up_names_the_earlier_question(self):
        html_ = self.line({'question': 'А в пятницу?', 'standalone': 'До скольки работает служба поддержки? А в пятницу?',
                           'relation': 'follow-joined', 'ref': 1})
        self.assertIn('уточнение к <a href="#t1">вопросу 1</a> «До скольки работает служба поддержки?»', html_)

    def test_date_carried_over(self):
        html_ = self.line({'question': 'А раньше?', 'standalone': 'А раньше выдавали пропуск?', 'relation': 'follow+date',
                           'ref': 1, 'asked_on': '2020-05-01'})
        self.assertIn('на дату 01.05.2020', html_)

    def test_new_topic_has_no_line(self):
        self.assertEqual(self.line({'question': 'Где бланк?', 'standalone': 'Где бланк?', 'relation': 'new'}), '')


if __name__ == '__main__':
    unittest.main()
