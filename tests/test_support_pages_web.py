"""Fault reports: page-level PDF, paraphrases, budget and purchase distractors,
two relevant regulations and a question with another independent action.

Fictional documents only; checked through the standard import and POST /ask.
"""
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'eval'))
from synthetic_docs import (PRINT_SERVICE_PAGES, PURCHASE_PAGES, SUPPORT_BUDGET, SUPPORT_FACTS,  # noqa: E402
                            SUPPORT_PAGES, write_pdf, write_xlsx)
from web_harness import Session  # noqa: E402

PARAPHRASES = [
    'Не работает принтер, куда обратиться и что написать?',
    'Сломалось МФУ — куда подать заявку и какие данные указать?',
    'Принтер не печатает: как сообщить о неисправности и что указать?',
    'Не включается компьютер: куда писать и какие сведения нужны?',
]
DISTRACTORS = ['Ремонт неисправных принтеров', 'Картриджи для принтеров', 'портал закупок', 'обоснование потребности']


def base(root):
    return [write_pdf(root / 'support.pdf', SUPPORT_PAGES), write_xlsx(root / 'budget.xlsx', SUPPORT_BUDGET),
            write_pdf(root / 'purchase.pdf', PURCHASE_PAGES)]


def with_print(root):
    return base(root) + [write_pdf(root / 'print.pdf', PRINT_SERVICE_PAGES)]


class SupportPagesWeb(unittest.TestCase):
    def cited(self, session, items, needle, title_part=None):
        found = [i for i in items if needle in i['text'] and i['links']]
        self.assertTrue(found, 'missing fact with citation: ' + needle)
        source = re.sub(r'\s+', ' ', session.open(found[0]['hrefs'][0]))
        self.assertIn(needle, source)
        # The cited item is highlighted inside the opened fragment.
        mark = re.search(r'<mark id="cited">(.*?)</mark>', source)
        self.assertIsNotNone(mark, 'cited unit is not highlighted')
        self.assertIn(needle, mark.group(1))
        self.assertIn(mark.group(1).strip(), found[0]['text'])
        return found[0]

    def test_paraphrases_page_level_pdf_and_distractors(self):
        with Session(base) as s:
            for q in PARAPHRASES:
                with self.subTest(question=q):
                    page, items = s.ask(q)
                    for key in ('channel', 'details', 'processing', 'not_repair'):
                        self.cited(s, items, SUPPORT_FACTS[key])
                    for text in DISTRACTORS:
                        self.assertNotIn(text, page)
                    # Primary processing is not presented as a repair deadline.
                    processing = page.index(SUPPORT_FACTS['processing'])
                    self.assertIn(SUPPORT_FACTS['not_repair'], page[processing:processing + 2000])
                    self.assertNotIn('Недостаточно данных', page)

    def test_repair_deadline_is_a_named_gap(self):
        with Session(base) as s:
            for q in ('Через сколько починят принтер?', 'Какой срок ремонта сломанного МФУ?'):
                with self.subTest(question=q):
                    page, items = s.ask(q)
                    self.cited(s, items, SUPPORT_FACTS['processing'])
                    self.cited(s, items, SUPPORT_FACTS['not_repair'])
                    self.assertTrue(any('срок устранения неисправности' in i['text'] and 'Недостаточно' in i['text'] for i in items))
                    for text in DISTRACTORS:
                        self.assertNotIn(text, page)

    def test_independent_second_action_is_a_named_gap(self):
        with Session(base) as s:
            page, items = s.ask('Не работает принтер: куда обратиться и как заказать новый картридж?')
            self.cited(s, items, SUPPORT_FACTS['channel'])
            self.assertTrue(any('Недостаточно данных для части вопроса' in i['text'] and 'картридж' in i['text'] for i in items))
            for text in DISTRACTORS:
                self.assertNotIn(text, page)

    def test_two_relevant_regulations_are_both_shown(self):
        with Session(with_print) as s:
            page, items = s.ask('Не работает принтер: куда обратиться и как заказать новый картридж?')
            self.cited(s, items, SUPPORT_FACTS['channel'])
            self.cited(s, items, 'через портал печати print.example.invalid')
            self.assertIn('Найдено несколько документов', page)
            cartridge = self.cited(s, items, 'Заявка на замену картриджа')
            self.assertIn('картридж', cartridge['headings'].get('h3', ''))
            self.assertFalse(any('Недостаточно данных для части вопроса' in i['text'] and 'картридж' in i['text'] for i in items))
            # Each document keeps its own processing time; they are not merged.
            self.cited(s, items, 'рассматривается в течение 2 рабочих дней')
            self.cited(s, items, SUPPORT_FACTS['processing'])

    def test_budget_and_purchase_alone_give_no_answer(self):
        def only_distractors(root):
            return [write_xlsx(root / 'budget.xlsx', SUPPORT_BUDGET), write_pdf(root / 'purchase.pdf', PURCHASE_PAGES)]
        with Session(only_distractors) as s:
            for q in PARAPHRASES[:2] + ['Через сколько починят принтер?']:
                with self.subTest(question=q):
                    page, items = s.ask(q)
                    self.assertIn('Недостаточно данных', page)
                    for text in DISTRACTORS:
                        self.assertNotIn(text, page)


if __name__ == '__main__':
    unittest.main()
