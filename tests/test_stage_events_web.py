"""Stages "before/after" an event named in the question, not only a trip.

Fictional documents through the standard import and POST /ask. The event of
the stages is taken from the question ("перед инвентаризацией … после неё"),
so procedures other than business trips are split into stages too.
"""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'eval'))
from synthetic_docs import TRIP_COMPETITORS, write_pdf  # noqa: E402
from web_harness import Session  # noqa: E402

INVENTORY = [['ПОРЯДОК проведения инвентаризации (учебный вымышленный документ)',
              '1. Перед инвентаризацией материально ответственное лицо сдаёт отчёт по форме ИНВ-19.',
              '2. Отчёт передают в бухгалтерию за 5 рабочих дней.',
              '3. По итогам инвентаризации комиссия оформляет акт по форме ИНВ-26.',
              '4. Акт утверждается в течение 3 рабочих дней.',
              '5. Если расхождений нет, акт подписывает только председатель комиссии.']]


HIRING = [['ИНСТРУКЦИЯ по приёму работников (учебный вымышленный документ)',
           '1. До приёма на работу',
           '1.1. Кандидат заполняет анкету по форме ПР-1.',
           '2. После оформления',
           '2.1. Работник проходит вводный инструктаж по охране труда.',
           '2.2. Руководитель знакомит работника с должностной инструкцией.']]


def build(root):
    return ([write_pdf(root / 'inventory.pdf', INVENTORY), write_pdf(root / 'hiring.pdf', HIRING)]
            + [write_pdf(root / n, p) for n, p in TRIP_COMPETITORS.items()])


class StageEventsWebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(build)

    @classmethod
    def tearDownClass(cls):
        cls.session.close()

    def staged(self, question):
        page, items = self.session.ask(question)
        def under(stage_heading, fact):
            return any(fact in i['text'] and i['headings'].get('h4', '').startswith(stage_heading) for i in items)
        return page, under

    def test_event_from_question(self):
        for question in ('Что сделать перед инвентаризацией и после неё?',
                         'Какие документы нужны до начала инвентаризации и по её итогам?'):
            with self.subTest(question=question):
                page, under = self.staged(question)
                self.assertTrue(under('До', 'ИНВ-19'), page[-1500:])
                self.assertTrue(under('До', 'за 5 рабочих дней'))
                self.assertTrue(under('После', 'ИНВ-26'))
                self.assertTrue(under('После', 'в течение 3 рабочих дней'))
                self.assertNotIn('путевой лист', page)
                self.assertNotIn('смету поездок', page)

    def test_items_of_a_heading_staged_section_are_steps(self):
        # No listed verb ("проходит"): the section heading carries the stage.
        page, under = self.staged('Что сделать до приёма на работу и после оформления?')
        self.assertTrue(under('После', 'проходит вводный инструктаж'), page[-1500:])
        self.assertTrue(under('До', 'по форме ПР-1'))
        self.assertNotIn('Недостаточно данных', page)

    def test_unknown_event_is_refused(self):
        page, _ = self.staged('Что нужно сделать до отпуска и после него?')
        self.assertIn('Недостаточно данных', page)
        self.assertNotIn('ИНВ-19', page)


if __name__ == '__main__':
    unittest.main()
