"""Exercise the synthetic rehearsal as a standalone operator command."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

class RehearsalTest(unittest.TestCase):
    def test_synthetic_rehearsal(self):
        completed=subprocess.run([sys.executable,'eval/rehearse_demo.py'],cwd=ROOT,check=True,capture_output=True,text=True)
        report=json.loads(completed.stdout)
        self.assertEqual(report['mode'],'extractive')
        self.assertEqual(report['versions'],2)
        self.assertEqual(report['question_samples'],5)
        self.assertTrue(all(report['checks'].values()))
        self.assertTrue(all(sample['http_status']==200 for sample in report['samples']))

if __name__ == '__main__': unittest.main()
