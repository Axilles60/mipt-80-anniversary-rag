"""Exercise evaluation reporting on the synthetic import/HTTP fixture."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

class EndToEndBenchmarkTest(unittest.TestCase):
    def test_report_covers_both_splits_and_scores_negative_case(self):
        run = subprocess.run([sys.executable, 'eval/measure_end_to_end.py'], cwd=ROOT,
                             check=True, capture_output=True, text=True)
        report = json.loads(run.stdout)
        self.assertEqual(set(report['sets']), {'tuning', 'holdout'})
        self.assertEqual(report['totals']['cases'], 10)
        for metric in ('cases', 'correct_answers', 'false_answers', 'missing_facts', 'citation_errors'):
            self.assertEqual(report['totals'][metric], sum(s[metric] for s in report['sets'].values()))
        cases = {r['id']: r for group in report['sets'].values() for r in group['results']}
        # A topical answer about issuing the pass does not establish cancellation.
        self.assertEqual(cases['H05']['expected_status'], 'insufficient')
        if cases['H05']['actual_status'] == 'answer':
            self.assertTrue(cases['H05']['false_answer'])
            self.assertTrue(cases['H05']['citation_errors'])
        else:
            self.assertEqual(cases['H05']['actual_status'], 'insufficient')
            self.assertFalse(cases['H05']['false_answer'])

if __name__ == '__main__': unittest.main()
