"""Run owner-authored, local evidence cases; output only aggregate to stdout."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.evidence import load, query

p=argparse.ArgumentParser()
p.add_argument('catalog'); p.add_argument('cases'); p.add_argument('--only',help='comma-separated case IDs'); p.add_argument('--report',required=True)
a=p.parse_args()
catalog=json.loads(Path(a.catalog).read_text(encoding='utf-8'))
cases=json.loads(Path(a.cases).read_text(encoding='utf-8'))
if a.only:
    selected=set(a.only.split(',')); cases=[c for c in cases if c['id'] in selected]
    if {c['id'] for c in cases} != selected: raise SystemExit('Unknown case ID')
if not cases: raise SystemExit('No cases supplied')
for case in cases:
    if 'owner_verified' in case:
        if not case['owner_verified'] or not case['question'].strip() or not case['reviewer'] or not case['review_date'] or not case['original_checksums_and_locators']:
            raise SystemExit('Owner review or required fields missing: '+case['id'])
        if not (case['expected_fragments'] or case.get('expect_question') or case.get('expect_insufficient')):
            raise SystemExit('Expected outcome missing: '+case['id'])
        if not case.get('required_facts') or not case.get('forbidden_conclusions'):
            raise SystemExit('Manual fact checklist missing: '+case['id'])
with closing(sqlite3.connect(':memory:')) as db:
    db.row_factory=sqlite3.Row
    load(db,catalog)
    results=[]
    for case in cases:
        result=query(db,case['question'],case.get('date'),case.get('conditions',{}))
        if result is None:
            results.append({'id':case['id'],'pass':bool(case.get('expect_insufficient')),'error':'topic not found'}); continue
        found={x['basis'] for x in result['items']}
        limits={x['basis'] for x in result['limits'] if x['basis']}
        ok=set(case.get('expected_fragments',[])) <= found and not (set(case.get('forbidden_fragments',[])) & found)
        ok=ok and set(case.get('expected_limit_fragments',[])) <= limits
        if case.get('expect_conflict'): ok=ok and bool(result['conflicts'])
        if case.get('expect_question'): ok=ok and bool(result['questions'])
        if case.get('expect_insufficient'): ok=ok and not result['items']
        results.append({'id':case['id'],'pass':ok,'result':result})
    report=Path(a.report); report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Cases:',len(results),'passed:',sum(x['pass'] for x in results),'failed:',sum(not x['pass'] for x in results))
    if any(not x['pass'] for x in results): sys.exit(1)
