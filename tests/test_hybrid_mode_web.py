"""The main web scenarios must hold in hybrid mode too (skipped without the model).

Runs the staged-trip, support and heading web tests in a subprocess with
NAVIGATOR_RETRIEVAL=hybrid. Found a real regression: the semantic gate dropped
fragments needed to choose the travel regulation.
"""
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def model_ready():
    from app import dense
    return dense.encoder() is not None


@unittest.skipUnless(model_ready(), 'local embedding model not installed (see README, hybrid search)')
class HybridModeWebTest(unittest.TestCase):
    def test_main_scenarios_in_hybrid_mode(self):
        env = dict(os.environ, NAVIGATOR_RETRIEVAL='hybrid')
        run = subprocess.run([sys.executable, '-m', 'unittest', 'tests.test_trip_pages_web',
                              'tests.test_support_pages_web', 'tests.test_heading_answer_web'],
                             cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
        self.assertEqual(run.returncode, 0, run.stderr[-3000:])


if __name__ == '__main__':
    unittest.main()
