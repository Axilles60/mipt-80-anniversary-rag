"""Synthetic, imported competing procedures and catalogue-backed limitations."""
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
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import urlopen
from xml.sax.saxutils import escape
import zipfile

from app.core import connect, imported_parts
from app.retrieval import imported
from app.sufficiency import supports
ROOT=Path(__file__).resolve().parents[1]
SUPPORT=[
 'При неисправности принтера сотрудник обращается в техническую службу через портал helpdesk.example.invalid.',
 'Заявка в техническую службу содержит местоположение, инвентарный номер и описание проблемы.',
 'Служба первично обрабатывает обращение за 12 рабочих часов; это не срок устранения неисправности.'
]
TRAVEL=[
 'До служебной поездки сотрудник подаёт заявление в кадровый отдел.',
 'До служебной поездки используйте форму TR-17.',
 'До служебной поездки заявление подают за 6 рабочих дней.',
 'После служебной поездки сотрудник направляет отчёт в архив.',
 'После служебной поездки используйте форму TR-28.',
 'После служебной поездки отчёт сдают в течение 4 рабочих дней.',
 'Реквизиты утверждения вымышленного регламента не заполнены.',
 'Приложения с формами TR-17 и TR-28 отсутствуют.'
]
DOCS={'support.docx':SUPPORT,'trip.docx':TRAVEL,
 'finance.docx':['Финансовая заявка для лицевого счёта содержит подразделение и сведения о расходах.','Срок обработки финансовой заявки утверждает финансовый отдел.'],
 'research.docx':['Заявку на исследовательский проект направляют в отдел исследований.','Для проекта необходимо указать описание, подразделение и сроки обработки заявки.'],
 'planning.docx':['Планирование служебной поездки включает смету расходов проекта и прогноз затрат.','После планирования поездки финансист направляет смету руководителю.']}

class Answer(HTMLParser):
 def __init__(self): super().__init__(); self.items=[]; self.item=None
 def handle_starttag(self,tag,attrs):
  if tag in ('li','p'): self.item={'text':'','links':[]}
  if tag=='a' and self.item is not None:
   href=dict(attrs).get('href','')
   if urlsplit(href).path=='/fragment': self.item['links'].append(href)
 def handle_data(self,data):
  if self.item is not None: self.item['text']+=data
 def handle_endtag(self,tag):
  if tag in ('li','p') and self.item is not None: self.items.append(self.item); self.item=None

class SyntheticDemoWeb(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup); self.root=Path(self.temp.name)
  self.env=dict(os.environ,NAVIGATOR_DB=str(self.root/'db'),HOST='127.0.0.1'); self.env.pop('NAVIGATOR_MODEL',None)
  manifest=self.root/'manifest.json'; manifest.write_text('[]'); paths=[]
  docs={name:list(paragraphs) for name,paragraphs in DOCS.items()}
  if self._testMethodName == 'test_missing_form_is_named': docs['trip.docx'].remove(TRAVEL[1])
  if self._testMethodName == 'test_missing_stage_is_named':
   docs['trip.docx'] = TRAVEL[:3] + TRAVEL[6:]
   del docs['planning.docx']
  for name, paragraphs in docs.items():
   path=self.root/name; body=''.join('<w:p><w:r><w:t>'+escape(x)+'</w:t></w:r></w:p>' for x in paragraphs)
   with zipfile.ZipFile(path,'w') as z: z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+body+'</w:body></w:document>')
   paths.append(str(path))
  subprocess.run([sys.executable,'-m','app','import',*paths,'--manifest',str(manifest)],env=self.env,cwd=ROOT,check=True,capture_output=True)
  with closing(connect(self.env['NAVIGATOR_DB'])) as db:
   self.rows={r['text']:dict(r) for r in db.execute('SELECT * FROM import_fragments')}
  with socket.socket() as s: s.bind(('127.0.0.1',0)); self.env['PORT']=str(s.getsockname()[1])
  self.base='http://127.0.0.1:'+self.env['PORT']; self.server=subprocess.Popen([sys.executable,'-m','app'],env=self.env,cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  self.addCleanup(self.stop)
  for _ in range(50):
   try: urlopen(self.base+'/',timeout=1).close(); break
   except OSError: time.sleep(.1)
  else: self.fail('server unavailable')
 def stop(self): self.server.terminate(); self.server.wait(timeout=5)
 def ask(self,q):
  page=urlopen(self.base+'/ask',data=urlencode({'question':q,'date':'2024-01-01'}).encode(),timeout=5).read().decode(); parsed=Answer(); parsed.feed(page); return page,parsed.items
 def assert_facts(self,items,texts):
  for text in texts:
   matches=[p for p in items if text in p['text'] and p['links']]
   self.assertTrue(matches,'Missing fact or adjacent citation: '+text)
   href=matches[0]['links'][0]; fid=parse_qs(urlsplit(href).query)['id'][0]
   self.assertEqual(fid,self.rows[text]['id'])
   source=urlopen(urljoin(self.base,href),timeout=5).read().decode()
   self.assertIn(text,source); self.assertIn(self.rows[text]['version'],source); self.assertIn(self.rows[text]['locator'],source)
 def load_catalog(self):
  docs=[dict(id='trip',title='Синтетическая поездка',signed='2020-01-01',effective='2020-01-01',valid_from='2020-01-01',valid_to=None,approval='incomplete')]
  frags=[dict(id=self.rows[t]['id'],document_id='trip',locator=self.rows[t]['locator'],text=t) for t in TRAVEL if t in self.rows]
  rules=[dict(id='r'+str(i),document_id='trip',fragment_id=self.rows[t]['id'],topic='trip',kind='norm',start='2020-01-01',end=None,conditions={},exceptions={}) for i,t in enumerate(TRAVEL[:6]) if t in self.rows]
  limits=[dict(id='approval',document_id='trip',topic='trip',description='Реквизиты утверждения не заполнены.',basis=self.rows[TRAVEL[6]]['id']),dict(id='appendix',document_id='trip',topic='trip',description='Формы TR-17 и TR-28 отсутствуют в приложениях.',basis=self.rows[TRAVEL[7]]['id'])]
  catalog=dict(documents=docs,fragments=frags,rules=rules,topics=[dict(id='trip',keywords=['поездк','командировк'])],relations=[],limits=limits)
  cp=self.root/'catalog.json'; cp.write_text(json.dumps(catalog,ensure_ascii=False)); subprocess.run([sys.executable,'-m','app','load-evidence',str(cp)],env=self.env,cwd=ROOT,check=True,capture_output=True)
 def test_candidates_and_filtering_are_observable(self):
  q='Печатающее устройство сломалось: куда сообщить и какие сведения нужны?'
  with closing(connect(self.env['NAVIGATOR_DB'])) as db:
   candidates=imported(db,q,100)
   candidate_texts=[r['text'] for r in candidates]
   for text in SUPPORT[:2]:
    self.assertIn(text,candidate_texts)
    self.assertTrue(supports(q,text))
   selected,missing,_=imported_parts(db,q)
   self.assertFalse(missing)
   self.assertTrue(set(SUPPORT[:2]).issubset({r['text'] for r in selected}))
   self.assertTrue(all(r['text'] not in DOCS['finance.docx']+DOCS['research.docx'] for r in selected))

 def test_support_direct_and_paraphrase(self):
  for q in ('Неисправен принтер: куда обратиться и что указать в заявке?', 'Печатающее устройство сломалось: куда сообщить и какие сведения нужны?', 'Не работает принтер, куда обратиться и что написать?'):
   with self.subTest(question=q):
    page,items=self.ask(q); self.assert_facts(items,SUPPORT[:2])
    for t in DOCS['finance.docx']+DOCS['research.docx']: self.assertNotIn(t,page)
    if SUPPORT[2] in page:
     self.assertIn('это не срок устранения',page); self.assert_facts(items,[SUPPORT[2]])
 def test_travel_ordinary_and_curated(self):
  questions=['Какие документы и сроки нужны до и после служебной поездки?',
             'Обычная многодневная командировка: когда подать документы и что делать после возвращения?']
  for linked in (False,True):
   if linked: self.load_catalog()
   for q in questions:
    with self.subTest(linked=linked,question=q):
     page,items=self.ask(q); self.assert_facts(items,TRAVEL[:6])
     self.assertIn('До поездки или события',page); self.assertIn('После возвращения или события',page)
     for t in DOCS['planning.docx']: self.assertNotIn(t,page)
     if linked:
      self.assertIn('Реквизиты утверждения не заполнены',page); self.assertIn('Формы TR-17 и TR-28 отсутствуют',page)
      for phrase, basis in [('Реквизиты утверждения не заполнены',TRAVEL[6]),('Формы TR-17 и TR-28 отсутствуют',TRAVEL[7])]:
       found=[p for p in items if phrase in p['text'] and p['links']]
       self.assertTrue(found)
       self.assertEqual(parse_qs(urlsplit(found[0]['links'][0]).query)['id'][0],self.rows[basis]['id'])
       self.assertIn(basis,urlopen(urljoin(self.base,found[0]['links'][0]),timeout=5).read().decode())
     else:
      self.assertIn('не проверены',page)
      self.assertNotIn('Реквизиты утверждения не заполнены',page)
      self.assertNotIn('Формы TR-17 и TR-28 отсутствуют',page)
 def test_missing_form_is_named(self):
  q='Какие документы и сроки нужны до и после служебной поездки?'
  for linked in (False,True):
   with self.subTest(linked=linked):
    if linked: self.load_catalog()
    page,items=self.ask(q)
    self.assertIn('Недостаточно данных для части',page); self.assertIn('до: номер формы',page)
    self.assert_facts(items,[TRAVEL[0],TRAVEL[2],TRAVEL[3],TRAVEL[4],TRAVEL[5]])

 def test_missing_stage_is_named(self):
  q='Какие документы и сроки нужны до и после служебной поездки?'
  for linked in (False,True):
   with self.subTest(linked=linked):
    if linked: self.load_catalog()
    page,items=self.ask(q)
    self.assert_facts(items,TRAVEL[:3])
    self.assertIn('Недостаточно данных для части',page)
    self.assertIn('после: действие',page)
    self.assertIn('после: срок',page)


if __name__=='__main__': unittest.main()
