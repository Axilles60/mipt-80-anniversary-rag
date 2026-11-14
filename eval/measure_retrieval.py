import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import re
def search(db,question):
 words=re.findall(r"[а-яёa-z0-9]+",question.lower())
 terms=[w for w in words if len(w)>=4][:30]
 if not terms: return []
 query=" OR ".join(chr(34)+w[:6]+chr(34)+"*" for w in terms)
 return db.execute("SELECT f.* FROM fragments_fts x JOIN fragments f ON f.id=x.id WHERE fragments_fts MATCH ? ORDER BY bm25(fragments_fts)",(query,)).fetchall()
from app.retrieval import retrieve

parser=argparse.ArgumentParser(); parser.add_argument('--baseline',action='store_true'); parser.add_argument('--cases',default='fixtures/retrieval/cases.json'); parser.add_argument('--report',required=True); args=parser.parse_args()
corpus=json.loads(Path('fixtures/retrieval/corpus.json').read_text()); cases=json.loads(Path(args.cases).read_text())
with closing(sqlite3.connect(':memory:')) as db:
    db.row_factory=sqlite3.Row
    db.executescript('CREATE TABLE fragments(id TEXT PRIMARY KEY,document_id TEXT,version TEXT,locator TEXT,text TEXT); CREATE VIRTUAL TABLE fragments_fts USING fts5(id UNINDEXED,text);')
    for x in corpus:
     db.execute('INSERT INTO fragments VALUES (?,?,?,?,?)',(x['id'],x['topic'],'1',x['id'],x['text'])); db.execute('INSERT INTO fragments_fts VALUES (?,?)',(x['id'],x['text']))
    results=[]
    for c in cases:
     rows=search(db,c['question'])[:3] if args.baseline else retrieve(db,c['question'],3)
     got=[r['id'] for r in rows]; expected=c['expected']; results.append({'id':c['id'],'expected':expected,'retrieved':got,'hits':len(set(got)&set(expected)),'total':len(expected)})
    p=Path(args.report); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps({'k':3,'cases':results},ensure_ascii=False,indent=2)+'\n')
    print('k=3; cases:',len(results),'expected fragments:',sum(x['total'] for x in results),'found:',sum(x['hits'] for x in results))
