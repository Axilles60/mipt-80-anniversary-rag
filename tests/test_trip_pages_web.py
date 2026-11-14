"""Staged procedure whose action, form, deadline and condition lie on different pages.

Fictional documents only. Each fact must be shown under its stage with a link
to the imported fragment that contains it; a missing facet must be named.
"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'eval'))
from diagnose_staged import QUESTIONS, diagnose  # noqa: E402
from synthetic_docs import TRIP_COMPETITORS, TRIP_FACTS, TRIP_PAGES  # noqa: E402


class TripPagesWeb(unittest.TestCase):
    def check_all_shown(self, fmt):
        for result in diagnose(fmt):
            with self.subTest(fmt=fmt, question=result['question']):
                lost = [(f['stage'], f['facet'], f['lost_at']) for f in result['facts'] if f['lost_at']]
                self.assertEqual(lost, [])
                self.assertEqual(result['gaps_reported'], [])
                page = result['html']
                for pages in TRIP_COMPETITORS.values():
                    for text in pages[0][1:]:
                        self.assertNotIn(text, page)
                # A range bound "до 700" is not a stage of the procedure.
                self.assertNotIn('до 700 условных единиц', page)
                self.assertIn('не заполнены реквизиты утверждения', page)
                self.assertIn('Приложение 2 упомянуто', page)
                after = page.split('После возвращения или события', 1)[1]
                self.assertIn('ФП-52', after)
                self.assertNotIn('ФП-11', after.split('Ограничение источника', 1)[0])

    def test_pdf_pages(self):
        self.check_all_shown('pdf')

    def test_docx_headings(self):
        self.check_all_shown('docx')

    def test_missing_after_form_is_named(self):
        pages = [list(p) for p in TRIP_PAGES]
        pages[2] = [x for x in pages[2] if 'ФП-52' not in x]
        facts = [f for f in TRIP_FACTS if f[2] != 'по ф. ФП-52']
        for fmt in ('pdf', 'docx'):
            result = diagnose(fmt, QUESTIONS[:1], pages, facts)[0]
            with self.subTest(fmt=fmt):
                self.assertEqual([f for f in result['facts'] if f['lost_at']], [])
                self.assertTrue(any('после: номер формы' in g for g in result['gaps_reported']), result['gaps_reported'])
                self.assertFalse(any('после: срок' in g for g in result['gaps_reported']))


if __name__ == '__main__':
    unittest.main()
