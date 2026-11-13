"""End-to-end test with a synthetic PDF, without either demo fixture."""
import hashlib
from contextlib import closing
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
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin
from urllib.request import urlopen

import fitz
from app.core import connect
from app.retrieval import imported

ROOT = Path(__file__).resolve().parents[1]
PHRASE = 'ZEPHYRQUARTZ'
TEXT = 'Procedure ZEPHYRQUARTZ: submit the fictional blue form to the local desk.'

class FragmentLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            href = dict(attrs).get('href', '')
            if href.startswith('/fragment?'):
                self.links.append(href)

class ImportedWebTest(unittest.TestCase):
    def test_import_search_web_citation_and_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / 'fiction.pdf'
            manifest = root / 'manifest.json'
            manifest.write_text('[]', encoding='utf-8')
            db_path = root / 'clean.sqlite3'
            pdf = fitz.open()
            page = pdf.new_page()
            page.insert_text((72, 72), TEXT)
            pdf.save(document)
            pdf.close()
            version = hashlib.sha256(document.read_bytes()).hexdigest()
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            with closing(sqlite3.connect(db_path)):
                pass  # Existing empty database, with no fixtures or schema.
            env = dict(os.environ, NAVIGATOR_DB=str(db_path), PORT=str(port))
            server = subprocess.Popen([sys.executable, '-m', 'app'], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            base = f'http://127.0.0.1:{port}'
            def ask(question):
                return urlopen(base + '/ask', data=urlencode({'question': question}).encode(), timeout=3).read().decode()
            try:
                for _ in range(50):
                    try:
                        with urlopen(base + '/', timeout=1):
                            break
                    except OSError:
                        time.sleep(.1)
                else:
                    self.fail('web server did not start')
                with self.assertRaises(HTTPError) as empty:
                    ask('   ')
                self.assertEqual(empty.exception.code, 400)
                self.assertIn('Введите вопрос', empty.exception.read().decode())
                self.assertIn('Недостаточно данных', ask(PHRASE))  # Empty database.
                result = subprocess.run([sys.executable, '-m', 'app', 'import', str(document), '--manifest', str(manifest), '--report', str(root / 'import.json')], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
                self.assertEqual(json.loads((root / 'import.json').read_text(encoding='utf-8')), {'ok': 1})
                with closing(connect(str(db_path))) as db:
                    hits = imported(db, PHRASE)
                    self.assertEqual(len(hits), 1)
                    fragment_id = hits[0]['id']
                    self.assertEqual(hits[0]['version'], version)
                    self.assertEqual(hits[0]['locator'], 'page/1/text-layer')
                response = ask(PHRASE)
                self.assertIn(TEXT, response)
                self.assertIn('Применимость и полнота не проверены', response)
                links = FragmentLinks()
                links.feed(response)
                self.assertEqual(len(links.links), 1)
                source = urlopen(urljoin(base, links.links[0]), timeout=3).read().decode()
                self.assertIn('fiction.pdf', source)
                self.assertIn(version, source)
                self.assertIn('page/1/text-layer', source)
                self.assertIn(TEXT, source)
                self.assertIn('id="selected"', source)
                # Re-import a changed original and reopen the previously obtained link.
                revised = fitz.open()
                revised_page = revised.new_page()
                revised_page.insert_text((72, 72), 'Procedure ZEPHYRQUARTZ: a fictional revised process uses a green form.')
                revised.save(document)
                revised.close()
                subprocess.run([sys.executable, '-m', 'app', 'import', str(document), '--manifest', str(manifest), '--report', str(root / 'revision.json')], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
                old_source = urlopen(urljoin(base, links.links[0]), timeout=3).read().decode()
                self.assertIn(version, old_source)
                self.assertIn(TEXT, old_source)
                self.assertIn('Недостаточно данных', ask('UNRELATEDSAPPHIRE'))
                with closing(sqlite3.connect(db_path)) as db, db:
                    db.execute('DROP TABLE import_fts')
                with self.assertRaises(HTTPError) as broken:
                    ask(PHRASE)
                self.assertEqual(broken.exception.code, 503)
                self.assertIn('Технический сбой', broken.exception.read().decode())
            finally:
                server.terminate()
                server.wait(timeout=5)

if __name__ == '__main__':
    unittest.main()
