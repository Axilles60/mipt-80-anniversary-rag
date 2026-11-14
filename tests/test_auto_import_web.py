"""Clean start: an empty index plus a folder of originals → answers through the web.

This is the container contract: the operator only puts documents into the
mounted folder and starts the service; import, OCR, fragments and the index are
produced by the code at start-up. Fictional documents only.
"""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import urlencode
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'eval'))
from synthetic_docs import SUPPORT_PAGES, trip_docx_paragraphs, write_docx, write_pdf  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); return s.getsockname()[1]


class AutoImportWebTest(unittest.TestCase):
    def start(self, env, log):
        server = subprocess.Popen([sys.executable, '-m', 'app'], cwd=ROOT, env=env,
                                  stdout=log, stderr=subprocess.STDOUT)
        base = 'http://127.0.0.1:' + env['PORT']
        for _ in range(600):
            try:
                urlopen(base + '/', timeout=1).close(); return server, base
            except OSError:
                if server.poll() is not None: break
                time.sleep(.1)
        server.terminate(); server.wait(timeout=10)
        self.fail('service did not start')

    def ask(self, base, question):
        return urlopen(base + '/ask', data=urlencode({'question': question}).encode(), timeout=60).read().decode()

    def test_folder_of_originals_is_imported_at_start(self):
        # On Windows the venv launcher's child may hold the log a moment after terminate().
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp); originals = root / 'originals'; (originals / 'нормы').mkdir(parents=True)
            write_pdf(originals / 'нормы' / 'support.pdf', SUPPORT_PAGES)
            trip = write_docx(root / 'trip.docx', trip_docx_paragraphs())
            with zipfile.ZipFile(originals / 'архив.zip', 'w') as z:
                z.write(trip, 'Положение о поездках.docx')
            (originals / 'broken.pdf').write_bytes(b'not a pdf')
            (originals / 'readme.txt').write_text('ignored', encoding='utf-8')
            env = dict(os.environ, NAVIGATOR_DB=str(root / 'index' / 'navigator.sqlite3'),
                       NAVIGATOR_IMPORT_DIR=str(originals), HOST='127.0.0.1', PORT=str(free_port()))
            for key in ('NAVIGATOR_MODEL', 'NAVIGATOR_RETRIEVAL', 'NAVIGATOR_MANIFEST'): env.pop(key, None)
            log_path = root / 'first.log'
            with open(log_path, 'w', encoding='utf-8') as log:
                server, base = self.start(env, log)
                try:
                    page = self.ask(base, 'Не работает принтер, куда обратиться и что написать?')
                    self.assertIn('portal.example.invalid/support', page)
                    page = self.ask(base, 'Обычная служебная поездка: когда подать документы и что делать после возвращения?')
                    self.assertIn('ФП-52', page)
                    self.assertIn('Недостаточно данных', self.ask(base, 'Как оформить ежегодный оплачиваемый отпуск?'))
                finally:
                    server.terminate(); server.wait(timeout=10)
            first = log_path.read_text(encoding='utf-8')
            self.assertIn('Import: 3 file(s)', first)          # .txt ignored
            self.assertIn('error 1', first)                   # damaged file isolated
            # Restart with the same index: nothing is re-imported.
            env['PORT'] = str(free_port())
            log_path = root / 'second.log'
            with open(log_path, 'w', encoding='utf-8') as log:
                server, base = self.start(env, log)
                try:
                    self.assertIn('portal.example.invalid/support',
                                  self.ask(base, 'Не работает принтер, куда обратиться и что написать?'))
                finally:
                    server.terminate(); server.wait(timeout=10)
            second = log_path.read_text(encoding='utf-8')
            self.assertIn('unchanged 2', second)

    def test_empty_folder_gives_refusal_not_failure(self):
        # On Windows the venv launcher's child may hold the log a moment after terminate().
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp); (root / 'originals').mkdir()
            env = dict(os.environ, NAVIGATOR_DB=str(root / 'index' / 'db.sqlite3'),
                       NAVIGATOR_IMPORT_DIR=str(root / 'originals'), HOST='127.0.0.1', PORT=str(free_port()))
            for key in ('NAVIGATOR_MODEL', 'NAVIGATOR_RETRIEVAL', 'NAVIGATOR_MANIFEST'): env.pop(key, None)
            with open(root / 'log', 'w', encoding='utf-8') as log:
                server, base = self.start(env, log)
                try:
                    self.assertIn('Недостаточно данных', self.ask(base, 'Как оформить командировку?'))
                finally:
                    server.terminate(); server.wait(timeout=10)


if __name__ == '__main__':
    unittest.main()
