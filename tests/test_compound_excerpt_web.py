"""Synthetic web regressions for shared context and complete evidence excerpts."""
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
from xml.sax.saxutils import escape
import zipfile

ROOT=Path(__file__).resolve().parents[1]

class Links(HTMLParser):
    def __init__(self): super().__init__(); self.hrefs=[]
    def handle_starttag(self,tag,attrs):
        href=dict(attrs).get('href','')
        if tag=='a' and href.startswith('/fragment?'): self.hrefs.append(href)

class CompoundExcerptWeb(unittest.TestCase):
    def check(self,text,question,linked,expect_partial=False,required=(),expect_refusal=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); path=root/'synthetic.docx'; manifest=root/'manifest.json'; manifest.write_text('[]')
            xml='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'+escape(text)+'</w:t></w:r></w:p><w:p><w:r><w:t>Контекст: синтетический пример.</w:t></w:r></w:p></w:body></w:document>'
            with zipfile.ZipFile(path,'w') as z: z.writestr('word/document.xml',xml)
            env=dict(os.environ,NAVIGATOR_DB=str(root/'db'),HOST='127.0.0.1'); env.pop('NAVIGATOR_MODEL',None)
            subprocess.run([sys.executable,'-m','app','import',str(path),'--manifest',str(manifest)],cwd=ROOT,env=env,check=True,capture_output=True)
            with closing(sqlite3.connect(env['NAVIGATOR_DB'])) as db:
                db.row_factory=sqlite3.Row; row=dict(db.execute('SELECT * FROM import_fragments WHERE text=?',(text,)).fetchone())
            if linked:
                catalog={'documents':[dict(id='d',title='fiction',signed='2020-01-01',effective='2020-01-01',valid_from='2020-01-01',valid_to=None,approval='confirmed')],
                  'fragments':[dict(id=row['id'],document_id='d',locator=row['locator'],text=row['text'])],
                  'topics':[dict(id='t',keywords=['пропуск'])],
                  'rules':[dict(id='r',document_id='d',fragment_id=row['id'],topic='t',kind='norm',start='2020-01-01',end=None,conditions={},exceptions={})], 'relations':[],'limits':[]}
                cp=root/'catalog.json'; cp.write_text(json.dumps(catalog,ensure_ascii=False),encoding='utf-8')
                subprocess.run([sys.executable,'-m','app','load-evidence',str(cp)],cwd=ROOT,env=env,check=True,capture_output=True)
            with socket.socket() as s: s.bind(('127.0.0.1',0)); env['PORT']=str(s.getsockname()[1])
            base='http://127.0.0.1:'+env['PORT']; server=subprocess.Popen([sys.executable,'-m','app'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                for _ in range(50):
                    try: urlopen(base+'/',timeout=1).close(); break
                    except OSError: time.sleep(.1)
                else: self.fail('server unavailable')
                page=urlopen(base+'/ask',data=urlencode({'question':question,'date':'2023-01-01','category':'гость'}).encode(),timeout=5).read().decode()
                for phrase in required: self.assertIn(phrase,page)
                if expect_refusal:
                    self.assertIn('Недостаточно контекста',page)
                    links=Links(); links.feed(page); self.assertEqual(links.hrefs,[])
                    self.assertNotIn(text[:100],page)
                    return
                if expect_partial:
                    self.assertIn('Недостаточно',page); self.assertIn('отменить пропуск для гостя',page)
                else: self.assertNotIn('Недостаточно',page)
                links=Links(); links.feed(page); self.assertTrue(links.hrefs)
                source=urlopen(urljoin(base,links.hrefs[0]),timeout=5).read().decode()
                self.assertIn(row['version'],source); self.assertIn(row['locator'],source)
                self.assertIn(text,source); self.assertIn('Контекст: синтетический пример.',source)
            finally: server.terminate(); server.wait(timeout=5)

    def test_joined_nouns_share_one_procedure(self):
        text='Для выдачи пропуска требуются заявление и согласование руководителя.'
        for linked in (False,True):
            with self.subTest(linked=linked): self.check(text,'Какие документы и согласования нужны для выдачи пропуска?',linked,required=['заявление и согласование руководителя'])

    def test_shared_object_two_actions_one_unanswered(self):
        text='Для гостя пропуск получают в бюро после подачи заявления.'
        for linked in (False,True):
            with self.subTest(linked=linked): self.check(text,'Как получить и отменить пропуск для гостя?',linked,expect_partial=True,required=['пропуск получают в бюро'])

    def test_long_fragment_keeps_late_exception(self):
        text='Пропуск выдают в бюро. '+('Синтетическое описание процедуры для внутренней проверки. '*13)+'Исключение: гостям пропуск не выдают.'
        self.assertGreater(text.index('Исключение'),500)
        for linked in (False,True):
            with self.subTest(linked=linked): self.check(text,'Как выдают пропуск?',linked,required=['Исключение: гостям пропуск не выдают.'])

    def test_oversized_unlinked_fragment_is_not_truncated_into_an_answer(self):
        text='Пропуск выдают в бюро. ' + ('Очень длинный вымышленный контекст. '*400) + 'Исключение: выдача запрещена.'
        self.assertGreater(len(text),12000)
        self.check(text,'Как выдают пропуск?',False,expect_refusal=True)

if __name__=='__main__': unittest.main()
