"""Exercise owner CLI workflow with fabricated documents and fabricated decisions."""
import hashlib,json,os,subprocess,sys,tempfile,zipfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='nav-owner-check-') as tmp:
 p=Path(tmp); manifest=[]; archive=p/'synthetic.zip'
 with zipfile.ZipFile(archive,'w') as bundle:
  for i in range(1,21):
   name=f'synthetic-{i:02}.docx'; doc=p/name
   with zipfile.ZipFile(doc,'w') as z:
    z.writestr('word/document.xml',f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Fictional procedure {i} for local owner workflow testing only.</w:t></w:r></w:p></w:body></w:document>')
   raw=doc.read_bytes(); bundle.writestr(name,raw); manifest.append({'id':f'SRC-{i:02}', 'name':name,'sha256':hashlib.sha256(raw).hexdigest()})
 manifest_path=p/'manifest.json'; manifest_path.write_text(json.dumps(manifest))
 env=dict(os.environ,NAVIGATOR_DB=str(p/'acceptance.sqlite3'))
 def run(*args): return subprocess.run([sys.executable,*map(str,args)],cwd=root,env=env,check=True,capture_output=True,text=True).stdout.strip()
 run('-m','app','import',archive,'--manifest',manifest_path,'--report',p/'import.json')
 run('-m','app','audit','--manifest',manifest_path,'--report',p/'audit.json')
 print('import:',(p/'import.json').read_text().strip())
 audit=json.loads((p/'audit.json').read_text()); print('audit entries:',len(audit),'matching checksums:',sum(x['matching_manifest_checksum'] for x in audit),'versioned:',sum(x['versions']==1 for x in audit)); assert len(audit)==20 and all(x['matching_manifest_checksum'] and x['versions']==1 for x in audit)
 cases=p/'owner-cases.json'; print(run('eval/prepare_owner_cases.py','--output',cases))
 entries=json.loads(cases.read_text()); print('ids:',len(entries),'distinct:',len({x['id'] for x in entries})); assert len(entries)==41
 for c in entries:
  c.update(question='Порядок заявки',date='2021-06-01',conditions={'категория':'А'},expected_fragments=['A1','A2'],required_facts=['synthetic fact'],forbidden_conclusions=['synthetic forbidden'],owner_verified=True,reviewer='synthetic reviewer',review_date='2026-11-13',original_checksums_and_locators=['synthetic only'])
 cases.write_text(json.dumps(entries,ensure_ascii=False))
 for args in [('--only','AC-DEMO-01'),('--only','AC-DEMO-02'),()]: print(run('eval/run_evidence.py','fixtures/evidence/synthetic.json',cases,*args,'--report',p/('report'+str(len(args))+str(args[:1])+'.json')))
