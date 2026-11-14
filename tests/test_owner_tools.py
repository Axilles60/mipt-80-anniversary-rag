import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
class OwnerToolsTest(unittest.TestCase):
    def test_pending_template_cannot_be_reported_as_passed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'cases.json'; report=Path(tmp)/'report.json'
            subprocess.run([sys.executable,'eval/prepare_owner_cases.py','--output',str(path)],cwd=ROOT,check=True,capture_output=True)
            cases=json.loads(path.read_text()); ids={x['id'] for x in cases}
            self.assertEqual(len(cases),41); self.assertEqual(len(ids),41); self.assertIn('AC-DEMO-01',ids); self.assertIn('AC-DEMO-02',ids)
            run=subprocess.run([sys.executable,'eval/run_evidence.py','fixtures/evidence/synthetic.json',str(path),'--only','AC-DEMO-01','--report',str(report)],cwd=ROOT,capture_output=True)
            self.assertNotEqual(run.returncode,0); self.assertFalse(report.exists())
            again=subprocess.run([sys.executable,'eval/prepare_owner_cases.py','--output',str(path)],cwd=ROOT,capture_output=True)
            self.assertNotEqual(again.returncode,0)

if __name__=='__main__': unittest.main()
