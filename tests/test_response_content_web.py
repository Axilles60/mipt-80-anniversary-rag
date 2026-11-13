"""Synthetic import-to-web checks for citations, sufficiency and partial answers."""
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
from urllib.parse import urlencode, urljoin
from urllib.request import urlopen
import zipfile
from app.evidence import keyword_matches

ROOT=Path(__file__).resolve().parents[1]

class Links(HTMLParser):
    def __init__(self): super().__init__(); self.hrefs=[]
    def handle_starttag(self,tag,attrs):
        href=dict(attrs).get('href','')
        if tag=='a' and href.startswith('/fragment?'): self.hrefs.append(href)

class ResponseContentWeb(unittest.TestCase):
    def test_keyword_boundaries_and_case(self):
        self.assertTrue(keyword_matches('регламент маяк','условия регламента маяк'))
        self.assertFalse(keyword_matches('регламент маяк','регламент маяковый'))
        self.assertTrue(keyword_matches('заявк','новая заявка'))

    def test_partial_and_selected_context_with_and_without_catalogue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); path=root/'source.docx'; manifest=root/'manifest.json'; manifest.write_text('[]')
            lines=['CENTAUR: подайте вымышленную форму в архив.', 'Контекст: архив работает только по вторникам.']
            xml='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+''.join('<w:p><w:r><w:t>'+x+'</w:t></w:r></w:p>' for x in lines)+'</w:body></w:document>'
            with zipfile.ZipFile(path,'w') as z: z.writestr('word/document.xml',xml)
            env=dict(os.environ,NAVIGATOR_DB=str(root/'db'),HOST='127.0.0.1'); env.pop('NAVIGATOR_MODEL',None)
            subprocess.run([sys.executable,'-m','app','import',str(path),'--manifest',str(manifest)],cwd=ROOT,env=env,check=True,capture_output=True)
            with closing(sqlite3.connect(env['NAVIGATOR_DB'])) as db:
                db.row_factory=sqlite3.Row; row=dict(db.execute('SELECT * FROM import_fragments WHERE text=?',(lines[0],)).fetchone())
            with socket.socket() as s: s.bind(('127.0.0.1',0)); env['PORT']=str(s.getsockname()[1])
            base='http://127.0.0.1:'+env['PORT']; server=subprocess.Popen([sys.executable,'-m','app'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            def ask(q): return urlopen(base+'/ask',data=urlencode({'question':q,'date':'2023-01-01'}).encode(),timeout=5).read().decode()
            try:
                for _ in range(50):
                    try: urlopen(base+'/',timeout=1).close(); break
                    except OSError: time.sleep(.1)
                else: self.fail('server unavailable')
                question='CENTAUR и как отменить ZORRO'
                page=ask(question)
                self.assertIn('Недостаточно данных для части вопроса: как отменить ZORRO',page)
                links=Links(); links.feed(page); self.assertEqual(len(links.hrefs),1)
                source=urlopen(urljoin(base,links.hrefs[0]),timeout=5).read().decode()
                self.assertIn(row['version'],source); self.assertIn(row['locator'],source)
                self.assertIn('id="selected"',source); self.assertIn(lines[0],source); self.assertIn(lines[1],source)
                catalog={'documents':[dict(id='d',title='d',signed='2020-01-01',effective='2020-01-01',valid_from='2020-01-01',valid_to=None,approval='confirmed')],
                         'fragments':[dict(id=row['id'],document_id='d',locator=row['locator'],text=row['text'])],
                         'topics':[dict(id='centaur',keywords=['centaur'])],
                         'rules':[dict(id='r',document_id='d',fragment_id=row['id'],topic='centaur',kind='norm',start='2020-01-01',end=None,conditions={},exceptions={})], 'relations':[],'limits':[]}
                cp=root/'catalog.json'; cp.write_text(json.dumps(catalog),encoding='utf-8')
                subprocess.run([sys.executable,'-m','app','load-evidence',str(cp)],cwd=ROOT,env=env,check=True,capture_output=True)
                page=ask(question)
                self.assertIn('Недостаточно подтверждённых сведений для этой части вопроса: как отменить ZORRO',page)
                self.assertIn('<ol>',page); self.assertIn(lines[0],page)
                page=ask('Кто оплачивает CENTAUR?')
                self.assertIn('Недостаточно',page); self.assertNotIn(lines[0],page)
                links=Links(); links.feed(page); self.assertEqual(links.hrefs,[])
            finally: server.terminate(); server.wait(timeout=5)

if __name__=='__main__': unittest.main()
