"""The gold question sets are consistently labelled (eval/check_gold.py)."""
from collections import Counter
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'eval'))
import check_gold  # noqa: E402


class GoldSetTest(unittest.TestCase):
    def test_labelling_is_consistent(self):
        _, problems = check_gold.check()
        self.assertEqual(problems, [])

    def test_covers_required_question_kinds(self):
        sets, _ = check_gold.check()
        for name, rows in sets.items():
            kinds = Counter(x['type'] for x in rows)
            expects = Counter(x['expect'] for x in rows)
            for kind in ('multihop', 'fact', 'false_premise', 'invalid', 'negative', 'offtopic', 'injection',
                         'conflict', 'historical', 'typo'):
                self.assertGreater(kinds[kind], 0, f'{name}: no {kind} questions')
            self.assertGreaterEqual(kinds['multihop'], 15, name)
            self.assertGreaterEqual(expects['insufficient'] + expects['off_topic'], 30, name)


if __name__ == '__main__':
    unittest.main()
