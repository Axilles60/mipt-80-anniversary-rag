"""Synthetic two-version lifecycle through the CLI importer and HTTP server."""
from contextlib import closing
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import urlencode, urljoin
from urllib.request import urlopen

import fitz
from app.core import connect

ROOT = Path(__file__).resolve().parents[1]

class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []
    def handle_starttag(self, tag, attrs):
        href = dict(attrs).get('href', '')
        if tag == 'a' and href.startswith('/fragment?'):
            self.hrefs.append(href)

class VersionPathsWeb(unittest.TestCase):
    def test_import_versions_and_confirmed_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = dict(os.environ, NAVIGATOR_DB=str(root/'db'))
            manifest = root/'manifest.json'
            manifest.write_text('[]')
            document = root/'regulation.pdf'
            def command(*args):
                subprocess.run([sys.executable, '-m', 'app', *map(str, args)],
                               cwd=ROOT, env=env, check=True, capture_output=True)
            def import_version(text):
                pdf = fitz.open()
                pdf.new_page().insert_text((50, 80), text)
                pdf.save(document)
                pdf.close()
                command('import', document, '--manifest', manifest, '--report', root/'import.json')
                self.assertEqual(json.loads((root/'import.json').read_text()), {'ok': 1})
                with closing(connect(env['NAVIGATOR_DB'])) as db:
                    return dict(db.execute('SELECT * FROM import_fragments ORDER BY rowid DESC LIMIT 1').fetchone())
            old_text = 'ALPHA procedure: submit the fictional application within ten working days.'
            new_text = 'ALPHA procedure: submit the fictional application within five working days.'
            old = import_version(old_text)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            env['PORT'] = str(port)
            server = subprocess.Popen([sys.executable, '-m', 'app'], cwd=ROOT, env=env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            base = f'http://127.0.0.1:{port}'
            def ask(date='2021-01-01'):
                return urlopen(base+'/ask', data=urlencode({'question':'ALPHA procedure', 'date':date}).encode(), timeout=3).read().decode()
            def links(page):
                parser = Links(); parser.feed(page); return parser.hrefs
            def load_catalog(relation, new_start='2022-01-01'):
                documents, fragments, rules = [], [], []
                for name, row, start in [('old', old, '2020-01-01'), ('new', new, new_start)]:
                    documents.append(dict(id=name, title=name, signed=start, effective=start,
                                          valid_from=start, valid_to=None, approval='confirmed'))
                    fragments.append(dict(id=row['id'], document_id=name, locator=row['locator'], text=row['text']))
                    rules.append(dict(id=name+'-rule', document_id=name, fragment_id=row['id'], topic='alpha',
                                      kind='norm', start=start, end=None, conditions={}, exceptions={}))
                relations = ([dict(id='relation', kind=relation, origin='new-rule', target='old-rule', basis=new['id'])]
                             if relation else [])
                catalog = dict(documents=documents, fragments=fragments, rules=rules,
                               topics=[dict(id='alpha', keywords=['alpha'])], relations=relations, limits=[])
                path = root/'catalog.json'; path.write_text(json.dumps(catalog), encoding='utf-8')
                command('load-evidence', path)
            try:
                for _ in range(50):
                    try: urlopen(base+'/', timeout=1).close(); break
                    except OSError: time.sleep(.1)
                else: self.fail('server unavailable')
                first = ask(); self.assertIn(old_text, first)
                self.assertIn('связь редакций не подтверждены', first)
                old_link = links(first)[0]
                new = import_version(new_text)
                self.assertEqual(old['source_id'], new['source_id'])
                self.assertNotEqual(old['version'], new['version'])
                for day in ('2021-01-01', '2023-01-01'):
                    plain = ask(day)
                    self.assertIn('Выбранная дата не определяет применимую редакцию', plain)
                source = urlopen(urljoin(base, old_link)).read().decode()
                self.assertIn(old['version'], source); self.assertIn(old_text, source)
                self.assertNotIn(new_text, source)
                load_catalog('amends_rule')
                historical = ask('2021-01-01')
                self.assertIn(old_text, historical); self.assertNotIn(new_text, historical)
                current = ask('2023-01-01')
                self.assertIn(new_text, current); self.assertNotIn(old_text, current)
                self.assertIn('Укажите дату', ask(''))
                load_catalog(None)
                uncertain = ask('2023-01-01')
                self.assertIn('Связь редакций не подтверждена', uncertain)
                self.assertNotIn(old_text, uncertain); self.assertNotIn(new_text, uncertain)
                self.assertEqual(len(links(uncertain)), 2)
                load_catalog('amends_rule', new_start=None)
                undated = ask('2023-01-01')
                self.assertIn('Дата действия одной из редакций не подтверждена', undated)
                self.assertNotIn(old_text, undated); self.assertNotIn(new_text, undated)
                load_catalog('conflict')
                conflict = ask('2023-01-01')
                self.assertIn('противоречие', conflict)
                self.assertIn('применимый вариант не установлен', conflict)
                self.assertIn(old_text, conflict); self.assertIn(new_text, conflict)
                self.assertGreaterEqual(len(links(conflict)), 2)
            finally:
                server.terminate(); server.wait(timeout=5)

if __name__ == '__main__': unittest.main()
