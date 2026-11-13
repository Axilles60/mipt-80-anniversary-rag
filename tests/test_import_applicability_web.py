import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from html.parser import HTMLParser
from urllib.parse import urlencode, unquote, urljoin
from urllib.request import urlopen

import fitz
from app.core import connect

ROOT = Path(__file__).resolve().parents[1]

class Links(HTMLParser):
    def __init__(self): super().__init__(); self.links=[]
    def handle_starttag(self, tag, attrs):
        if tag == 'a' and dict(attrs).get('href','').startswith('/fragment?'):
            self.links.append(dict(attrs)['href'])

class ImportedApplicabilityWeb(unittest.TestCase):
    def test_versions_conflict_unknown_and_partial(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); dbpath=root/'db'; manifest=root/'manifest.json'; manifest.write_text('[]')
            env=dict(os.environ,NAVIGATOR_DB=str(dbpath))
            def load_pdf(name, texts):
                path=root/name; pdf=fitz.open()
                for text in texts:
                    page=pdf.new_page(); page.insert_text((60,60),text)
                pdf.save(path); pdf.close()
                subprocess.run([sys.executable,'-m','app','import',str(path),'--manifest',str(manifest),'--report',str(root/'report.json')],cwd=ROOT,env=env,check=True,capture_output=True)
                with closing(connect(str(dbpath))) as db:
                    row=db.execute('select source_id,version from imports where filename=? order by rowid desc limit 1',(name,)).fetchone()
                    return [dict(r) for r in db.execute('select * from import_fragments where source_id=? and version=? order by rowid',(row['source_id'],row['version']))]
            old=load_pdf('regulation.pdf',['ALPHA old rule: submit an imaginary application within ten days.', 'ALPHA unchanged rule: attach the imaginary blue cover page.'])
            new=load_pdf('regulation.pdf',['ALPHA amended rule: submit an imaginary application within five days.'])
            conflict=load_pdf('conflict.pdf',['BETA first rule: submit the imaginary item within seven days.','BETA second rule: submit the imaginary item within nine days.'])
            unknown=load_pdf('unknown.pdf',['OMEGA imaginary procedure has no established date of commencement.'])
            category_b=load_pdf('category.pdf',['ALPHA category B: submit the imaginary green request to the desk.'])
            future=load_pdf('future.pdf',[f'ALPHA future fictional procedure paragraph number {n}, not in force yet.' for n in range(5)])
            docs=[]; fragments=[]; rules=[]
            def doc(docid,rows,start,topic,conditions=None):
                docs.append(dict(id=docid,title=docid,signed=start,effective=start,valid_from=start,valid_to=None,approval='confirmed'))
                for ix,row in enumerate(rows):
                    fragments.append(dict(id=row['id'],document_id=docid,locator=row['locator'],text=row['text']))
                    rules.append(dict(id=f'{docid}-{ix}',document_id=docid,fragment_id=row['id'],topic=topic,kind='norm',start=start,end=None,conditions=conditions or {},exceptions={}))
            doc('old',old,'2020-01-01','alpha',{'категория':'A'})
            doc('new',new,'2022-01-01','alpha',{'категория':'A'})
            doc('conflict',conflict,'2020-01-01','beta')
            doc('unknown',unknown,None,'omega')
            doc('category',category_b,'2020-01-01','alpha',{'категория':'B'})
            doc('future',future,'2030-01-01','alpha',{'категория':'A'})
            catalog={'documents':docs,'fragments':fragments,'rules':rules,'topics':[{'id':x,'keywords':[x]} for x in ['alpha','beta','omega']],
                     'relations':[{'id':'amend','kind':'amends_rule','origin':'new-0','target':'old-0','basis':new[0]['id']}, {'id':'conflict','kind':'conflict','origin':'conflict-0','target':'conflict-1','basis':conflict[0]['id']}, {'id':'gap','kind':'gap','origin':'new','target':'old','basis':new[0]['id']}],
                     'limits':[]}
            cpath=root/'catalog.json'; cpath.write_text(json.dumps(catalog),encoding='utf-8')
            subprocess.run([sys.executable,'-m','app','load-evidence',str(cpath)],cwd=ROOT,env=env,check=True,capture_output=True)
            with socket.socket() as sock: sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
            env['PORT']=str(port); server=subprocess.Popen([sys.executable,'-m','app'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            base=f'http://127.0.0.1:{port}'
            def ask(question,date='2021-01-01',category='A'):
                return urlopen(base+'/ask',data=urlencode({'question':question,'date':date,'category':category}).encode(),timeout=3).read().decode()
            def ids(page):
                parser=Links(); parser.feed(page); return [unquote(x.split('id=',1)[1]) for x in parser.links]
            def verify_sources(page, rows):
                hrefs=LinksFor(page)
                for row in rows:
                    href=next(h for h in hrefs if row['id'] in unquote(h))
                    source=urlopen(urljoin(base,href),timeout=3).read().decode()
                    self.assertIn(row['version'],source); self.assertIn(row['locator'],source)
                    self.assertIn(row['text'].strip(),source)
            try:
                for _ in range(50):
                    try: urlopen(base+'/',timeout=1).close(); break
                    except OSError: time.sleep(.1)
                else: self.fail('server failed to start')
                before=ask('alpha'); before_ids=ids(before)
                self.assertIn(old[0]['id'],before_ids); self.assertIn(old[1]['id'],before_ids); self.assertNotIn(new[0]['id'],before_ids)
                verify_sources(before,old)
                old_href=next(x for x in LinksFor(before) if old[0]['id'] in unquote(x))
                after=ask('alpha','2023-01-01'); after_ids=ids(after)
                self.assertIn(new[0]['id'],after_ids); self.assertIn(old[1]['id'],after_ids); self.assertNotIn(old[0]['id'],after_ids)
                self.assertIn('attach the imaginary blue cover page', after)
                verify_sources(after,[new[0],old[1]])
                self.assertIn('Цепочка редакций неполна',after)
                self.assertTrue(all(r['id'] not in after_ids for r in future))
                result_b=ask('alpha','2023-01-01','B'); self.assertIn(category_b[0]['id'],ids(result_b)); self.assertNotIn('within five days',result_b)
                source=urlopen(urljoin(base,old_href)).read().decode(); self.assertIn(old[0]['version'],source); self.assertIn(old[0]['locator'],source); self.assertIn('ten days',source)
                result=ask('beta'); self.assertIn('противоречие',result); self.assertTrue({r['id'] for r in conflict}.issubset(set(ids(result)))); verify_sources(result,conflict)
                result=ask('omega'); self.assertIn('Дата начала действия не подтверждена',result); self.assertIn(unknown[0]['id'],ids(result))
                self.assertIn('Уточните условие',ask('alpha',category=''))
                partial=ask('alpha и gamma'); self.assertIn(old[0]['id'],ids(partial)); self.assertIn('Недостаточно подтверждённых сведений для этой части',partial)
                with closing(connect(str(dbpath))) as db, db:
                    db.execute('update evidence_fragments set text=? where id=?',('FABRICATED TEXT',old[0]['id']))
                self.assertNotIn('FABRICATED TEXT',ask('alpha'))
            finally:
                server.terminate(); server.wait(timeout=5)

def LinksFor(page):
    parser=Links(); parser.feed(page); return parser.links

if __name__=='__main__': unittest.main()
