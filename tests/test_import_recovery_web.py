"""Import failure recovery through the real CLI and HTTP endpoints; synthetic only."""
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

ROOT = Path(__file__).resolve().parents[1]

class Links(HTMLParser):
    def __init__(self): super().__init__(); self.hrefs=[]
    def handle_starttag(self, tag, attrs):
        href=dict(attrs).get('href','')
        if tag=='a' and href.startswith('/fragment?'): self.hrefs.append(href)

def docx(path, text):
    xml=('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
         '<w:body><w:p><w:r><w:t>'+escape(text)+'</w:t></w:r></w:p></w:body></w:document>')
    with zipfile.ZipFile(path,'w') as z: z.writestr('word/document.xml',xml)

class ImportRecoveryWeb(unittest.TestCase):
    def test_retry_package_faults_index_repair_and_web_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); dbpath=root/'db.sqlite'; manifest=root/'manifest.json'; manifest.write_text('[]')
            env=dict(os.environ,NAVIGATOR_DB=str(dbpath),HOST='127.0.0.1'); env.pop('NAVIGATOR_MODEL',None)
            doc=root/'regulation.docx'; other=root/'other.docx'; archive=root/'package.zip'
            old_text='ALPHA fictional procedure: file an imaginary application within ten days.'
            new_text='ALPHA fictional procedure: file an imaginary application within five days.'
            docx(doc,old_text); docx(other,'OMEGA fictional process: send the imaginary token to the desk.')
            counter=0
            def command(*args, expected=0):
                nonlocal counter
                counter+=1; report=root/f'report-{counter}.json'
                result=subprocess.run([sys.executable,'-m','app','import',*map(str,args),'--manifest',str(manifest),'--report',str(report)],cwd=ROOT,env=env,capture_output=True,text=True)
                self.assertEqual(result.returncode,expected,result.stderr)
                return json.loads(report.read_text())
            def dbrows(sql,params=()):
                with closing(sqlite3.connect(dbpath)) as db:
                    db.row_factory=sqlite3.Row
                    return [dict(row) for row in db.execute(sql,params)]
            def statement(sql):
                with closing(sqlite3.connect(dbpath)) as db, db: db.execute(sql)
            def check_old(old):
                self.assertEqual(dbrows('SELECT text FROM import_fragments WHERE id=?',(old['id'],))[0]['text'],old_text)
                self.assertEqual(len(dbrows('SELECT * FROM import_fts WHERE id=?',(old['id'],))),1)
            self.assertEqual(command(doc),{'review':1})
            old=dbrows('SELECT * FROM import_fragments')[0]
            self.assertEqual(command(doc),{'unchanged':1})
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr(doc.name,doc.read_bytes()); z.writestr(other.name,other.read_bytes()); z.writestr('broken.docx',b'not a docx')
            self.assertEqual(command(archive,expected=1),{'unchanged':1,'review':1,'error':1})
            self.assertEqual(len(dbrows('SELECT * FROM imports')),2); check_old(old)
            with socket.socket() as sock: sock.bind(('127.0.0.1',0)); env['PORT']=str(sock.getsockname()[1])
            base='http://127.0.0.1:'+env['PORT']
            server=subprocess.Popen([sys.executable,'-m','app'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            def ask(day): return urlopen(base+'/ask',data=urlencode({'question':'ALPHA','date':day}).encode(),timeout=5).read().decode()
            try:
                for _ in range(50):
                    try: urlopen(base+'/',timeout=1).close(); break
                    except OSError: time.sleep(.1)
                else: self.fail('server unavailable')
                page=ask('2021-01-01'); self.assertIn(old_text,page)
                links=Links(); links.feed(page); old_link=links.hrefs[0]
                # The unchanged document used to be skipped even with missing FTS rows.
                with closing(sqlite3.connect(dbpath)) as db, db: db.execute('DELETE FROM import_fts WHERE id=?',(old['id'],))
                statement("CREATE TRIGGER fail_repair BEFORE INSERT ON import_fts_content BEGIN SELECT RAISE(ABORT,'simulated repair failure'); END")
                self.assertEqual(command(doc,expected=1),{'error':1})
                self.assertIn(old_text,urlopen(urljoin(base,old_link),timeout=5).read().decode())
                statement('DROP TRIGGER fail_repair')
                self.assertEqual(command(doc),{'reindexed':1}); check_old(old)
                self.assertIn(old_text,ask('2021-01-01'))
                with closing(sqlite3.connect(dbpath)) as db, db:
                    db.execute('UPDATE import_fts SET text=? WHERE id=?',('stale text',old['id']))
                    db.execute('INSERT INTO import_fts VALUES (?,?)',(old['id'],'duplicate'))
                self.assertEqual(command(doc),{'reindexed':1}); check_old(old)
                self.assertEqual(dbrows('SELECT text FROM import_fts WHERE id=?',(old['id'],))[0]['text'],old_text)
                self.assertEqual(command(doc),{'unchanged':1})
                docx(doc,new_text)
                # Force a document write failure, then an FTS write failure after
                # the original and fragment inserts; both must roll back fully.
                statement("CREATE TRIGGER fail_document BEFORE INSERT ON imports BEGIN SELECT RAISE(ABORT,'simulated document failure'); END")
                self.assertEqual(command(doc,expected=1),{'error':1}); statement('DROP TRIGGER fail_document')
                self.assertEqual(len(dbrows('SELECT * FROM imports')),2); check_old(old)
                statement("CREATE TRIGGER fail_index BEFORE INSERT ON import_fts_content BEGIN SELECT RAISE(ABORT,'simulated index failure'); END")
                self.assertEqual(command(doc,expected=1),{'error':1}); statement('DROP TRIGGER fail_index')
                self.assertEqual(len(dbrows('SELECT * FROM imports')),2)
                self.assertEqual(len(dbrows('SELECT * FROM import_fragments')),2)
                self.assertEqual(len(dbrows('SELECT * FROM import_fts')),2); check_old(old)
                self.assertIn(old_text,ask('2021-01-01'))
                self.assertEqual(command(doc),{'review':1})
                new=dbrows('SELECT * FROM import_fragments WHERE text=?',(new_text,))[0]
                self.assertEqual(old['source_id'],new['source_id']); self.assertNotEqual(old['version'],new['version'])
                self.assertEqual(command(doc),{'unchanged':1})
                docs=[]; fragments=[]; rules=[]
                for name,row,start in [('old',old,'2020-01-01'),('new',new,'2022-01-01')]:
                    docs.append(dict(id=name,title=name,signed=start,effective=start,valid_from=start,valid_to=None,approval='confirmed'))
                    fragments.append(dict(id=row['id'],document_id=name,locator=row['locator'],text=row['text']))
                    rules.append(dict(id=name+'-rule',document_id=name,fragment_id=row['id'],topic='alpha',kind='norm',start=start,end=None,conditions={},exceptions={}))
                catalog=dict(documents=docs,fragments=fragments,rules=rules,topics=[dict(id='alpha',keywords=['alpha'])],relations=[dict(id='amend',kind='amends_rule',origin='new-rule',target='old-rule',basis=new['id'])],limits=[])
                catalog_path=root/'catalog.json'; catalog_path.write_text(json.dumps(catalog))
                subprocess.run([sys.executable,'-m','app','load-evidence',str(catalog_path)],cwd=ROOT,env=env,check=True,capture_output=True)
                self.assertIn(old_text,ask('2021-01-01'))
                current=ask('2023-01-01'); self.assertIn(new_text,current); self.assertNotIn(old_text,current)
                source=urlopen(urljoin(base,old_link),timeout=5).read().decode()
                self.assertIn(old['version'],source); self.assertIn(old_text,source); self.assertNotIn(new_text,source)
            finally:
                server.terminate(); server.wait(timeout=5)

    def test_process_killed_during_write_can_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); dbpath=root/'db'; manifest=root/'manifest.json'; manifest.write_text('[]'); document=root/'regulation.docx'
            env=dict(os.environ,NAVIGATOR_DB=str(dbpath)); docx(document,'AURORA old synthetic rule for an imaginary application.')
            def cmd(): return [sys.executable,'-m','app','import',str(document),'--manifest',str(manifest),'--report',str(root/'report.json')]
            subprocess.run(cmd(),cwd=ROOT,env=env,check=True,capture_output=True)
            docx(document,'AURORA revised synthetic rule for an imaginary application.')
            with closing(sqlite3.connect(dbpath)) as db, db:
                db.execute('''CREATE TRIGGER pause_write AFTER INSERT ON imports BEGIN
                  SELECT sum(x) FROM (WITH RECURSIVE counter(x) AS
                  (VALUES(1) UNION ALL SELECT x+1 FROM counter WHERE x<100000000) SELECT x FROM counter);
                END''')
            process=subprocess.Popen(cmd(),cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            locked=False
            try:
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    if process.poll() is not None: self.fail('import ended before interruption')
                    try:
                        with closing(sqlite3.connect(dbpath,timeout=0)) as db, db: db.execute('BEGIN IMMEDIATE'); db.rollback()
                    except sqlite3.OperationalError as exc:
                        if 'locked' in str(exc): locked=True; break
                        raise
                    time.sleep(.01)
                self.assertTrue(locked,'import did not enter write transaction')
                time.sleep(.2)  # Wait past schema setup while the synthetic trigger holds the write lock.
                self.assertIsNone(process.poll(), 'write completed before interruption')
            finally:
                process.kill(); process.wait(timeout=5)
            with closing(sqlite3.connect(dbpath)) as db, db:
                self.assertEqual(db.execute('SELECT count(*) FROM imports').fetchone()[0],1)
                self.assertEqual(db.execute('SELECT count(*) FROM import_fragments').fetchone()[0],1)
                self.assertEqual(db.execute('SELECT count(*) FROM import_fts').fetchone()[0],1)
                db.execute('DROP TRIGGER pause_write')
            subprocess.run(cmd(),cwd=ROOT,env=env,check=True,capture_output=True)
            self.assertEqual(json.loads((root/'report.json').read_text()),{'review':1})
            with closing(sqlite3.connect(dbpath)) as db, db:
                self.assertEqual(db.execute('SELECT count(*) FROM imports').fetchone()[0],2)
                self.assertEqual(db.execute('SELECT count(*) FROM import_fragments').fetchone()[0],2)
                self.assertEqual(db.execute('SELECT count(*) FROM import_fts').fetchone()[0],2)
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')

if __name__ == '__main__': unittest.main()
