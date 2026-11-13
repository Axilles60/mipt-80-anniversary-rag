import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class SearchMetricsTest(unittest.TestCase):
    def test_web_holdout_exact_and_false_positive_regressions(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports=[]
            for baseline in (True,False):
                output=Path(tmp)/('before.json' if baseline else 'after.json')
                cmd=[sys.executable,'eval/measure_web_search.py'] + (['--baseline'] if baseline else []) + ['--cases','fixtures/web_search/holdout.json','--report',str(output)]
                subprocess.run(cmd,cwd=ROOT,check=True,capture_output=True)
                reports.append(json.loads(output.read_text()))
            before,after=reports
            self.assertEqual(before['k'],after['k'])
            self.assertEqual(before['expected_fragments'],after['expected_fragments'])
            self.assertGreaterEqual(after['found'],before['found'])
            self.assertLessEqual(after['false_positive'],before['false_positive'])
            self.assertLess(after['exact_failures'],before['exact_failures'])

    def test_sufficiency_and_partial_through_import_and_web(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'report.json'
            subprocess.run([sys.executable, 'eval/measure_web_search.py', '--cases',
                            'fixtures/web_search/holdout.json', '--report', str(output)],
                           cwd=ROOT, check=True, capture_output=True)
            result = {item['id']: item for item in json.loads(output.read_text())['results']}
            for case in ('H07', 'H08'):
                self.assertEqual(result[case]['retrieved'], [])
                self.assertTrue(result[case]['insufficient'])
            subprocess.run([sys.executable, 'eval/measure_web_search.py', '--cases',
                            'fixtures/web_search/sufficiency_holdout.json', '--report', str(output)],
                           cwd=ROOT, check=True, capture_output=True)
            result = {item['id']: item for item in json.loads(output.read_text())['results']}
            self.assertEqual(result['S10']['retrieved'], ['HALL'])
            self.assertTrue(result['S10']['insufficient'])

if __name__ == '__main__': unittest.main()
