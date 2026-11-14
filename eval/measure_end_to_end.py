"""Synthetic end-to-end benchmark: real CLI import, catalogue and HTTP endpoints."""
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
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import urlopen
from xml.sax.saxutils import escape
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'fixtures/end_to_end'

class Response(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.part=None
    def handle_starttag(self, tag, attrs):
        if tag in ('p', 'li'):
            self.part={'text':'', 'links':[]}
        if tag == 'a' and self.part is not None:
            href=dict(attrs).get('href', '')
            if urlsplit(href).path == '/fragment': self.part['links'].append(href)
    def handle_data(self, data):
        if self.part is not None: self.part['text'] += data
    def handle_endtag(self, tag):
        if tag in ('p', 'li') and self.part is not None:
            self.parts.append(self.part); self.part=None

class Selected(HTMLParser):
    def __init__(self):
        super().__init__(); self.depth=0; self.text=''
    def handle_starttag(self, tag, attrs):
        if self.depth: self.depth += 1
        elif tag == 'div' and dict(attrs).get('id') == 'selected': self.depth = 1
    def handle_data(self, data):
        if self.depth: self.text += data
    def handle_endtag(self, tag):
        if self.depth: self.depth -= 1

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--report', help='Optional JSON output file'); parser.add_argument('--split', choices=['tuning','holdout','both'], default='both')
    args=parser.parse_args()
    corpus=json.loads((FIXTURES/'corpus.json').read_text(encoding='utf-8'))
    sets={name:json.loads((FIXTURES/(name+'.json')).read_text(encoding='utf-8')) for name in (('tuning','holdout') if args.split == 'both' else (args.split,))}
    with tempfile.TemporaryDirectory(prefix='navigator-e2e-') as tmp:
        root=Path(tmp); dbpath=root/'db.sqlite'; manifest=root/'manifest.json'; manifest.write_text('[]')
        env=dict(os.environ, NAVIGATOR_DB=str(dbpath), HOST='127.0.0.1')
        env.pop('NAVIGATOR_MODEL', None)
        fragments={}; docs=[]; doc_for={}
        for number, document in enumerate(corpus['documents']):
            path=root/document['file']; w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
            body=''.join('<w:p><w:r><w:t>'+escape(f['text'])+'</w:t></w:r></w:p>' for f in document['fragments'])
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('word/document.xml', f'<w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>')
            subprocess.run([sys.executable,'-m','app','import',str(path),'--manifest',str(manifest),'--report',str(root/'import.json')],cwd=ROOT,env=env,check=True,capture_output=True)
            imported=json.loads((root/'import.json').read_text())
            if imported != {'review':1}: raise RuntimeError(f'Unexpected import status: {imported}')
            with closing(sqlite3.connect(dbpath)) as db:
                db.row_factory=sqlite3.Row
                for fragment in document['fragments']:
                    rows=db.execute('SELECT * FROM import_fragments WHERE text=?',(fragment['text'],)).fetchall()
                    if len(rows)!=1: raise RuntimeError('Fragment must resolve uniquely: '+fragment['id'])
                    fragments[fragment['id']]=dict(rows[0]); doc_for[fragment['id']]=f'doc-{number}'
            docs.append(dict(id=f'doc-{number}',title=document['file'],signed='2020-01-01',effective='2020-01-01',valid_from='2020-01-01',valid_to=None,approval='confirmed'))
        catalogue={'documents':docs,'fragments':[], 'topics':corpus['topics'], 'rules':[], 'relations':[], 'limits':[]}
        for key, row in fragments.items():
            catalogue['fragments'].append(dict(id=row['id'],document_id=doc_for[key],locator=row['locator'],text=row['text']))
        for rule in corpus['rules']:
            key=rule['fragment']
            catalogue['rules'].append(dict(id=rule['id'],document_id=doc_for[key],fragment_id=fragments[key]['id'],topic=rule['topic'],kind='norm',start=rule['start'],end=None,conditions=rule.get('conditions',{}),exceptions=rule.get('exceptions',{})))
        for relation in corpus['relations']:
            catalogue['relations'].append({**relation,'basis':fragments[relation['basis']]['id']})
        catalog_path=root/'catalog.json'; catalog_path.write_text(json.dumps(catalogue,ensure_ascii=False),encoding='utf-8')
        subprocess.run([sys.executable,'-m','app','load-evidence',str(catalog_path)],cwd=ROOT,env=env,check=True,capture_output=True)
        labels={row['id']:label for label,row in fragments.items()}
        with socket.socket() as sock: sock.bind(('127.0.0.1',0)); env['PORT']=str(sock.getsockname()[1])
        base='http://127.0.0.1:'+env['PORT']
        server=subprocess.Popen([sys.executable,'-m','app'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                try: urlopen(base+'/',timeout=1).close(); break
                except OSError: time.sleep(.1)
            else: raise RuntimeError('Web server unavailable')
            output={}
            for name,cases in sets.items():
                results=[]
                for case in cases:
                    page=urlopen(base+'/ask',data=urlencode({k:case[k] for k in ('question','date','category')}).encode(),timeout=5).read().decode()
                    parsed=Response(); parsed.feed(page)
                    parts=parsed.parts
                    insufficient=any('Недостаточно' in p['text'] for p in parts)
                    answer=[p for p in parts if not p['text'].startswith(('Ограничение:', 'Обнаружено противоречие', 'Недостаточно', 'Применимость и полнота', 'Укажите дату', 'Уточните условие'))]
                    status='partial' if answer and insufficient else 'answer' if answer else 'insufficient' if insufficient else 'other'
                    text=' '.join(p['text'] for p in answer)
                    forbidden=[x for x in case['forbidden_conclusions'] if x.lower() in text.lower()]
                    missing=[fact for fact in case['required_facts'] if fact['text'].lower() not in text.lower()]
                    errors=[]; opened={}
                    for part in parts:
                        for href in part['links']:
                            fid=parse_qs(urlsplit(href).query).get('id',[''])[0]; label=labels.get(fid)
                            if label not in case['allowed_sources']:
                                errors.append({'type':'disallowed_source','source':label or fid})
                            try:
                                source=urlopen(urljoin(base,href),timeout=5).read().decode()
                                selected=Selected(); selected.feed(source)
                                if not label or fragments[label]['text'] not in selected.text: raise ValueError('selected fragment mismatch')
                                opened[label]=True
                            except Exception as exc: errors.append({'type':'unopenable_or_wrong_fragment','source':label,'error':str(exc)})
                    for part in answer:
                        if not part['links']:
                            errors.append({'type':'uncited_answer', 'text':part['text']})
                    for fact in case['required_facts']:
                        if fact in missing: continue
                        matched=any(fact['text'].lower() in p['text'].lower() and any(labels.get(parse_qs(urlsplit(h).query).get('id',[''])[0])==fact['source'] for h in p['links']) for p in answer)
                        if not matched or not opened.get(fact['source']): errors.append({'type':'unsupported_fact','fact':fact['text'],'source':fact['source']})
                    false=bool(forbidden or (case['expected_status']=='insufficient' and answer) or (case['expected_status']=='partial' and status=='answer'))
                    correct=status==case['expected_status'] and not missing and not forbidden and not errors
                    results.append(dict(id=case['id'],expected_status=case['expected_status'],actual_status=status,correct=correct,false_answer=false,missing_facts=missing,forbidden_found=forbidden,citation_errors=errors,opened_sources=sorted(opened)))
                output[name]={'cases':len(cases),'correct_answers':sum(x['correct'] for x in results),'false_answers':sum(x['false_answer'] for x in results),'missing_facts':sum(len(x['missing_facts']) for x in results),'citation_errors':sum(len(x['citation_errors']) for x in results),'results':results}
            metrics=('cases','correct_answers','false_answers','missing_facts','citation_errors')
            totals={key:sum(result[key] for result in output.values()) for key in metrics}
            report={'mode':'extractive','data':'synthetic','totals':totals,'sets':output}
            print(json.dumps(report,ensure_ascii=False,indent=2))
            if args.report:
                target=Path(args.report); target.parent.mkdir(parents=True,exist_ok=True)
                with target.open('x',encoding='utf-8') as f: json.dump(report,f,ensure_ascii=False,indent=2); f.write('\n')
        finally:
            server.terminate(); server.wait(timeout=5)

if __name__=='__main__': main()
