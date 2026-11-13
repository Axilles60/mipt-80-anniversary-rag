"""Offline synthetic rehearsal: real CLI import, catalogue, HTTP and citations."""
import argparse
from contextlib import closing
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import platform
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]

class Links(HTMLParser):
    def __init__(self): super().__init__(); self.hrefs=[]
    def handle_starttag(self, tag, attrs):
        href=dict(attrs).get('href','')
        if tag=='a' and urlsplit(href).path=='/fragment': self.hrefs.append(href)

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--report', help='New JSON report path (will not overwrite)')
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='nav-rehearsal-') as tmp:
        root=Path(tmp); dbpath=root/'index.sqlite3'; manifest=root/'manifest.json'; manifest.write_text('[]')
        doc=root/'fictional-regulation.docx'; env=dict(os.environ,NAVIGATOR_DB=str(dbpath),HOST='127.0.0.1')
        env.pop('NAVIGATOR_MODEL',None)
        old_text='Регламент 571-2021. Для заявки сотрудник направляет синюю форму в отдел за десять рабочих дней.'
        new_text='Регламент 571-2023. Для заявки сотрудник направляет синюю форму в отдел за пять рабочих дней.'
        def import_version(text,number):
            xml='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'+text+'</w:t></w:r></w:p></w:body></w:document>'
            with zipfile.ZipFile(doc,'w') as z: z.writestr('word/document.xml',xml)
            size=doc.stat().st_size; report=root/f'import-{number}.json'
            start=time.perf_counter()
            subprocess.run([sys.executable,'-m','app','import',str(doc),'--manifest',str(manifest),'--report',str(report)],cwd=ROOT,env=env,check=True,capture_output=True)
            elapsed=round((time.perf_counter()-start)*1000,2)
            statuses=json.loads(report.read_text())
            if statuses != {'review':1}: raise RuntimeError(f'unexpected import: {statuses}')
            with closing(sqlite3.connect(dbpath)) as db:
                db.row_factory=sqlite3.Row
                rows=db.execute('SELECT * FROM import_fragments WHERE text=?',(text,)).fetchall()
                if len(rows)!=1: raise RuntimeError('fragment missing or duplicated')
                row=dict(rows[0])
            return row, {'file_size_bytes':size,'elapsed_ms':elapsed,'statuses':statuses}
        old, first=import_version(old_text,1)
        with socket.socket() as sock: sock.bind(('127.0.0.1',0)); env['PORT']=str(sock.getsockname()[1])
        base='http://127.0.0.1:'+env['PORT']
        server=subprocess.Popen([sys.executable,'-m','app'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                try: urlopen(base+'/',timeout=1).close(); break
                except OSError: time.sleep(.1)
            else: raise RuntimeError('web server unavailable')
            samples=[]
            def ask(label,question,date=''):
                start=time.perf_counter()
                with urlopen(base+'/ask',data=urlencode({'question':question,'date':date}).encode(),timeout=5) as response:
                    page=response.read().decode(); status=response.status
                samples.append({'scenario':label,'question':question,'date':date or None,'latency_ms':round((time.perf_counter()-start)*1000,2),'http_status':status})
                links=Links(); links.feed(page)
                return page,links.hrefs
            def source_for(hrefs,row,expected):
                for href in hrefs:
                    if parse_qs(urlsplit(href).query).get('id',[''])[0] == row['id']:
                        page=urlopen(urljoin(base,href),timeout=5).read().decode()
                        if all(x in page for x in (row['version'],row['locator'],expected,'id="selected"')): return href
                raise RuntimeError('citation, version, locator or text missing')
            page, links=ask('exact','Регламент 571-2021')
            if old_text not in page: raise RuntimeError('exact query failed')
            old_href=source_for(links,old,old_text)
            page,links=ask('paraphrase','Куда направить заявку по регламенту 571-2021?')
            if old_text not in page: raise RuntimeError('paraphrase failed')
            source_for(links,old,old_text)
            page,links=ask('unknown','Как отменить заявку по регламенту 571-2021?')
            if 'Недостаточно данных' not in page or links: raise RuntimeError('unknown query gave an answer')
            new, second=import_version(new_text,2)
            if old['source_id'] != new['source_id'] or old['version'] == new['version']: raise RuntimeError('incorrect version identity')
            docs=[]; fragments=[]; rules=[]
            for label,row,start in [('old',old,'2021-01-01'),('new',new,'2023-01-01')]:
                docs.append(dict(id=label,title=label,signed=start,effective=start,valid_from=start,valid_to=None,approval='confirmed'))
                fragments.append(dict(id=row['id'],document_id=label,locator=row['locator'],text=row['text']))
                rules.append(dict(id=label+'-rule',document_id=label,fragment_id=row['id'],topic='rule',kind='norm',start=start,end=None,conditions={},exceptions={}))
            catalog=dict(documents=docs,fragments=fragments,rules=rules,topics=[dict(id='rule',keywords=['регламент 571'])],relations=[dict(id='amend',kind='amends_rule',origin='new-rule',target='old-rule',basis=new['id'])],limits=[])
            cp=root/'catalog.json'; cp.write_text(json.dumps(catalog,ensure_ascii=False),encoding='utf-8')
            subprocess.run([sys.executable,'-m','app','load-evidence',str(cp)],cwd=ROOT,env=env,check=True,capture_output=True)
            page,links=ask('historical','Регламент 571','2022-01-01')
            if old_text not in page or new_text in page: raise RuntimeError('historical version failed')
            source_for(links,old,old_text)
            page,links=ask('updated','Регламент 571','2024-01-01')
            if new_text not in page or old_text in page: raise RuntimeError('updated version failed')
            source_for(links,new,new_text)
            old_page=urlopen(urljoin(base,old_href),timeout=5).read().decode()
            if old_text not in old_page or old['version'] not in old_page or old['locator'] not in old_page or new_text in old_page: raise RuntimeError('old citation changed')
            result={'mode':'extractive', 'data':'synthetic','environment':{'platform':platform.platform(),'machine':platform.machine(),'python':platform.python_version(),'sqlite':sqlite3.sqlite_version,'logical_cpus_visible':os.cpu_count()},
                    'database':'new temporary SQLite', 'documents':1,'versions':2,'imports':[first,second], 'question_samples':len(samples),'samples':samples,
                    'checks':{'exact':True,'paraphrase':True,'no_answer':True,'old_version':True,'new_version':True,'old_link_after_update':True,'citation_version_locator_and_text':True},
                    'latency_scope':'one warm loopback POST per scenario; includes request and response, excludes citation opening; import includes CLI startup'}
            rendered=json.dumps(result,ensure_ascii=False,indent=2)+'\n'; print(rendered,end='')
            if args.report:
                with Path(args.report).open('x',encoding='utf-8') as f: f.write(rendered)
        finally:
            server.terminate(); server.wait(timeout=5)

if __name__=='__main__': main()
