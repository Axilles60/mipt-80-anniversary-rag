"""Synthetic benchmark through batch import and the actual POST /ask path."""
import argparse
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
from urllib.parse import parse_qs, unquote, urlencode, urlsplit
from urllib.request import urlopen
from xml.sax.saxutils import escape
import zipfile

ROOT=Path(__file__).resolve().parents[1]
K=3
class Links(HTMLParser):
 def __init__(self): super().__init__(); self.ids=[]
 def handle_starttag(self,tag,attrs):
  if tag=='a':
   u=urlsplit(dict(attrs).get('href',''))
   if u.path=='/fragment': self.ids.extend(parse_qs(u.query).get('id',[]))

def run(cases, corpus, baseline=False, before_sufficiency=False):
 with tempfile.TemporaryDirectory() as t:
  root=Path(t); db=root/'db'; manifest=root/'manifest.json'; manifest.write_text('[]'); paths=[]
  for item in corpus:
   p=root/item['file']; text=escape(item['text']); w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
   with zipfile.ZipFile(p,'w') as z: z.writestr('word/document.xml',f'<w:document xmlns:w="{w}"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
   paths.append(str(p))
  env=dict(os.environ,NAVIGATOR_DB=str(db)); subprocess.run([sys.executable,'-m','app','import',*paths,'--manifest',str(manifest),'--report',str(root/'import.json')],cwd=ROOT,env=env,check=True,capture_output=True)
  report=json.loads((root/'import.json').read_text()); assert report=={'review':len(corpus)},report
  with closing(sqlite3.connect(db)) as con:
   source_map={r[0]:r[1] for r in con.execute('SELECT f.id,d.filename FROM import_fragments f JOIN imports d ON f.source_id=d.source_id AND f.version=d.version')}
  filenames={x['file']:x['id'] for x in corpus}; labels={fid:filenames[name] for fid,name in source_map.items()}
  with socket.socket() as s: s.bind(('127.0.0.1',0)); port=s.getsockname()[1]
  env['PORT']=str(port); command = ([sys.executable, 'eval/baseline_search_server.py'] if baseline else [sys.executable, 'eval/before_sufficiency_server.py'] if before_sufficiency else [sys.executable, '-m', 'app']); server=subprocess.Popen(command,cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); base=f'http://127.0.0.1:{port}'
  try:
   for _ in range(50):
    try: urlopen(base+'/',timeout=1).close(); break
    except OSError: time.sleep(.1)
   else: raise RuntimeError('server unavailable')
   result=[]
   for case in cases:
    page=urlopen(base+'/ask',data=urlencode({'question':case['question']}).encode(),timeout=5).read().decode()
    links=Links(); links.feed(page); got=[labels[x] for x in links.ids if x in labels][:K]
    result.append({'id':case['id'],'type':case['type'],'expected':case['expected'],'retrieved':got,'missing':sorted(set(case['expected'])-set(got)),'false_positive':case['type']=='negative' and bool(got),'forbidden':sorted(set(case.get('forbidden',[]))&set(got)), 'insufficient': 'Недостаточно данных' in page})
   return result
  finally: server.terminate(); server.wait(timeout=5)

p=argparse.ArgumentParser(); p.add_argument('--baseline',action='store_true'); p.add_argument('--before-sufficiency',action='store_true'); p.add_argument('--cases',required=True); p.add_argument('--report',required=True); args=p.parse_args()
corpus=json.loads((ROOT/'fixtures/web_search/corpus.json').read_text()); cases=json.loads(Path(args.cases).read_text()); result=run(cases,corpus,args.baseline,args.before_sufficiency)
expected=sum(len(x['expected']) for x in result); found=sum(len(x['expected'])-len(x['missing']) for x in result); negatives=sum(x['type']=='negative' for x in result); false=sum(x['false_positive'] for x in result); bad_exact=sum(bool(x['missing'] or x['forbidden']) for x in result if x['type']=='exact')
report={'k':K,'cases':len(result),'expected_fragments':expected,'found':found,'negative_cases':negatives,'false_positive':false,'exact_failures':bad_exact,'results':result}; target=Path(args.report); target.parent.mkdir(parents=True,exist_ok=True); target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(f'k={K}, cases={len(result)}, recall={found}/{expected}, false={false}/{negatives}, exact_failures={bad_exact}')
