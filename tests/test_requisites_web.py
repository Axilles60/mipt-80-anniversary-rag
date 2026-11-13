"""Versions chosen from the documents' own requisites, without a curated catalogue.

Fictional DOCX acts through the standard import and POST /ask: an old order,
a new order that repeals it from a stated date, and an unrelated document.
"""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'eval'))
from synthetic_docs import write_docx  # noqa: E402
from web_harness import Session  # noqa: E402

OLD = ['ПРИКАЗ от 12.02.2015 № 76-1',
       'Об утверждении порядка выдачи пропусков (учебный вымышленный документ)',
       '1. Пропуск выдаётся бюро пропусков в течение 5 рабочих дней по заявлению работника.']
NEW = ['ПРИКАЗ от 19.03.2021 № 138-1',
       'О новом порядке выдачи пропусков (учебный вымышленный документ)',
       '1. Порядок вводится в действие с 01.04.2021.',
       '2. Признать утратившим силу приказ от 12.02.2015 № 76-1.',
       '3. Пропуск выдаётся бюро пропусков в течение 2 рабочих дней по заявлению работника.']
CANTEEN = ['Распорядок работы столовой (вымышленный)', 'Столовая работает с 12 до 15 часов.']
QUESTION = 'В какой срок бюро выдаёт пропуск?'


def build(root):
    return [write_docx(root / 'old.docx', OLD), write_docx(root / 'new.docx', NEW), write_docx(root / 'canteen.docx', CANTEEN)]


class RequisitesWebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(build)

    @classmethod
    def tearDownClass(cls):
        cls.session.close()

    def test_current_question_uses_the_repealing_act(self):
        page, _ = self.session.ask(QUESTION)
        self.assertIn('в течение 2 рабочих дней', page)
        self.assertNotIn('в течение 5 рабочих дней', page)
        self.assertIn('утратившим силу', page)            # the exclusion is explained…
        self.assertIn('№ 138-1 от 19.03.2021', page)       # …with the requisites read from the text

    def test_historical_date_uses_the_old_act(self):
        page, _ = self.session.ask(QUESTION, date='2020-06-01')
        self.assertIn('в течение 5 рабочих дней', page)
        self.assertNotIn('в течение 2 рабочих дней', page)
        self.assertIn('с 01.04.2021 он признан утратившим силу', page)   # the later act is named

    def test_date_after_entry_into_force(self):
        page, _ = self.session.ask(QUESTION, date='2021-06-01')
        self.assertIn('в течение 2 рабочих дней', page)
        self.assertNotIn('в течение 5 рабочих дней', page)


if __name__ == '__main__':
    unittest.main()
