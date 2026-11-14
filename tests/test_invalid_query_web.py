"""Meaningless queries get a refusal, never a dropped connection (ANS-10, CASE-T14).

Found by the expanded gold set (iteration 21): «???» crashed the request handler
in document selection, so the client received no response at all.
"""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'eval'))
from synthetic_docs import write_docx  # noqa: E402
from web_harness import Session  # noqa: E402

RULES = ['ПРАВИЛА внутреннего распорядка (учебный вымышленный документ)',
         '1. Рабочий день начинается в 9:00 и заканчивается в 18:00.',
         '2. Перерыв на обед — с 13:00 до 14:00.']
INVALID = ['???', '!!!', '-----', '.......', '…', '0']


class InvalidQueryWebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(lambda root: [write_docx(root / 'rules.docx', RULES)])

    @classmethod
    def tearDownClass(cls):
        cls.session.close()

    def test_meaningless_queries_are_refused(self):
        for query in INVALID:
            with self.subTest(query=query):
                page, items = self.session.ask(query)
                self.assertIn('Недостаточно данных', page)
                self.assertNotIn('13:00', page)

    def test_service_still_answers_afterwards(self):
        self.session.ask('???')
        page, _ = self.session.ask('Когда перерыв на обед?')
        self.assertIn('13:00', page)


if __name__ == '__main__':
    unittest.main()
