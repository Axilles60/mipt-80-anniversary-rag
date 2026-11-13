"""Create a local, unverified checklist; never read original document content."""
import argparse
import json
from pathlib import Path

root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(); p.add_argument('--output',required=True); args=p.parse_args()
cases=[]
for item in json.loads((root/'fixtures/owner_scenarios.json').read_text(encoding='utf-8')):
    cases.append({'id':item['id'],'spec':item['spec'], 'question':'', 'date':None,'conditions':{},
                  'expected_fragments':[],'forbidden_fragments':[],'expected_limit_fragments':[],
                  'expect_conflict':False,'expect_question':False,'expect_insufficient':False,
                  'required_facts':[],'forbidden_conclusions':[], 'owner_verified':False,
                  'reviewer':'','review_date':'','original_checksums_and_locators':[]})
output=Path(args.output); output.parent.mkdir(parents=True,exist_ok=True)
with output.open('x',encoding='utf-8') as f: json.dump(cases,f,ensure_ascii=False,indent=2); f.write('\n')
print('Local unverified cases prepared:',len(cases))
