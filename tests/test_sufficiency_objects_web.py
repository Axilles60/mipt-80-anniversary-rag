"""Two isolated, synthetic import and POST /ask paths for evidence sufficiency."""
from contextlib import closing
from html.parser import HTMLParser
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
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin
from urllib.request import urlopen
from xml.sax.saxutils import escape
import zipfile

ROOT = Path(__file__).resolve().parents[1]

class Links(HTMLParser):
    def __init__(self): super().__init__(); self.hrefs=[]
    def handle_starttag(self,tag,attrs):
        href=dict(attrs).get('href','')
        if tag=='a' and href.startswith('/fragment?'): self.hrefs.append(href)

class SufficiencyObjectWeb(unittest.TestCase):
    def run_case(self, text, question, linked, expected, partial=False, technical_failure=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); path=root/'synthetic.docx'; manifest=root/'manifest.json'; manifest.write_text('[]')
            xml='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'+escape(text)+'</w:t></w:r></w:p></w:body></w:document>'
            with zipfile.ZipFile(path,'w') as z: z.writestr('word/document.xml',xml)
            env=dict(os.environ,NAVIGATOR_DB=str(root/'db'),HOST='127.0.0.1'); env.pop('NAVIGATOR_MODEL',None)
            subprocess.run([sys.executable,'-m','app','import',str(path),'--manifest',str(manifest),'--report',str(root/'import.json')],cwd=ROOT,env=env,check=True,capture_output=True)
            self.assertEqual(json.loads((root/'import.json').read_text()),{'review':1})
            with closing(sqlite3.connect(env['NAVIGATOR_DB'])) as db:
                db.row_factory=sqlite3.Row; row=dict(db.execute('SELECT * FROM import_fragments').fetchone())
            if linked:
                catalog={'documents':[dict(id='d',title='d',signed='2020-01-01',effective='2020-01-01',valid_from='2020-01-01',valid_to=None,approval='confirmed')],
                         'fragments':[dict(id=row['id'],document_id='d',locator=row['locator'],text=row['text'])],
                         'topics':[dict(id='t',keywords=['пропуск'])],
                         'rules':[dict(id='r',document_id='d',fragment_id=row['id'],topic='t',kind='norm',start='2020-01-01',end=None,conditions={},exceptions={})], 'relations':[],'limits':[]}
                cp=root/'catalog.json'; cp.write_text(json.dumps(catalog,ensure_ascii=False),encoding='utf-8')
                subprocess.run([sys.executable,'-m','app','load-evidence',str(cp)],cwd=ROOT,env=env,check=True,capture_output=True)
            with socket.socket() as sock: sock.bind(('127.0.0.1',0)); env['PORT']=str(sock.getsockname()[1])
            base='http://127.0.0.1:'+env['PORT']; server=subprocess.Popen([sys.executable,'-m','app'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                for _ in range(50):
                    try: urlopen(base+'/',timeout=1).close(); break
                    except OSError: time.sleep(.1)
                else: self.fail('server unavailable')
                if technical_failure:
                    with closing(sqlite3.connect(env['NAVIGATOR_DB'])) as db, db:
                        db.execute('DROP TABLE ' + ('evidence_rules' if linked else 'import_fts'))
                    with self.assertRaises(HTTPError) as error:
                        urlopen(base+'/ask',data=urlencode({'question':question,'date':'2023-01-01'}).encode(),timeout=5)
                    self.assertEqual(error.exception.code,503)
                    self.assertIn('Технический сбой',error.exception.read().decode())
                    return
                with urlopen(base+'/ask',data=urlencode({'question':question,'date':'2023-01-01'}).encode(),timeout=5) as response: page=response.read().decode()
                links=Links(); links.feed(page)
                if expected:
                    self.assertIn(text,page); self.assertTrue(links.hrefs)
                    source=urlopen(urljoin(base,links.hrefs[0]),timeout=5).read().decode()
                    self.assertIn(text,source); self.assertIn(row['version'],source); self.assertIn(row['locator'],source)
                else:
                    self.assertIn('Недостаточно',page); self.assertEqual(links.hrefs,[]); self.assertNotIn(text,page)
                if partial:
                    self.assertIn('Недостаточно',page); self.assertIn('в какой срок',page)
            finally: server.terminate(); server.wait(timeout=5)

    def test_unrelated_payment_and_missing_time(self):
        cases=[('Пропуск выдают в бюро. Оплату парковки принимает касса','Кто оплачивает пропуск?'),
               ('Пропуск выдают в бюро','В какой срок выдают пропуск?'),
               ('Оплату парковки осуществляет сотрудник, пропуск выдают в бюро.','Кто оплачивает пропуск?'),
               ('Оплата парковки не требуется. Пропуск выдают в бюро.','Кто оплачивает пропуск?'),
               ('Пропуск выдают в бюро. Парковку оформляют за три дня.','В какой срок выдают пропуск?'),
               ('Пропуск оплачивается при выдаче.','Кто оплачивает пропуск?'),
               ('Оплату пропуска осуществляют.','Кто оплачивает пропуск?'),
               ('Оплату парковки осуществляет пропускной отдел.','Кто оплачивает пропуск?'),
               ('Оплата парковки не требуется при оформлении пропуска.','Кто оплачивает пропуск?'),
               ('Оплату парковки осуществляет сотрудник при оформлении пропуска.','Кто оплачивает пропуск?'),
               ('Парковку оформляют за три дня после выдачи пропуска.','В какой срок выдают пропуск?')]
        for linked in (False,True):
            for text,question in cases:
                with self.subTest(linked=linked,question=question): self.run_case(text,question,linked,False)

    def test_confirmed_payer_deadline_and_explicit_waiver(self):
        cases=[('Оплату пропуска осуществляет сотрудник.','Кто оплачивает пропуск?'),
               ('Сотрудник оплачивает пропуск.','Кто оплачивает пропуск?'),
               ('Пропуск выдают в течение трёх рабочих дней.','В какой срок выдают пропуск?'),
               ('Оплата пропуска не требуется.','Кто оплачивает пропуск?')]
        for linked in (False,True):
            for text,question in cases:
                with self.subTest(linked=linked,text=text): self.run_case(text,question,linked,True)

    def test_technical_failure_is_not_insufficient(self):
        for linked in (False,True):
            with self.subTest(linked=linked): self.run_case('Пропуск выдают в бюро','Кто оплачивает пропуск?',linked,False,technical_failure=True)

    def test_partial_answer(self):
        for linked in (False,True):
            with self.subTest(linked=linked): self.run_case('Пропуск выдают в бюро','Куда выдают пропуск и в какой срок выдают пропуск?',linked,True,partial=True)

if __name__=='__main__': unittest.main()
