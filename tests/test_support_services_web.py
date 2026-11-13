"""Faults of IT services (not only devices) and the "where to report" guard.

Fictional documents through the standard import and POST /ask: the paged
support regulation, a budget with service and lift lines. A fault of a service
must reach the support regulation; a budget cell is never an answer to "where to
report", including for a fault outside IT.
"""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'eval'))
from synthetic_docs import SUPPORT_PAGES, write_pdf, write_xlsx  # noqa: E402
from web_harness import Session  # noqa: E402

BUDGET = [['Статья', 'Описание', 'Сумма'],
          ['Связь', 'Обслуживание VPN-шлюза и удалённого доступа подрядчиком', 90000],
          ['Информационные системы', 'Сопровождение информационной системы кадрового учёта', 150000],
          ['Здание', 'Ремонт лифтового оборудования подрядчиком', 200000]]
CHANNEL = 'portal.example.invalid/support'


def build(root):
    return [write_pdf(root / 'support.pdf', SUPPORT_PAGES), write_xlsx(root / 'budget.xlsx', BUDGET)]


class SupportServicesWebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(build)
        cls.meta = {f['id']: f['metadata'] for f in cls.session.fragments()}

    @classmethod
    def tearDownClass(cls):
        cls.session.close()

    def ask(self, question):
        page, items = self.session.ask(question)
        cells = [i for item in items for i in item['links'] if '"type": "cell"' in self.meta.get(i, '')]
        return page, cells

    def test_service_faults_reach_support_regulation(self):
        for question in ('Не работает VPN, куда писать?',
                         'Нет доступа к корпоративной почте — кому сообщить?',
                         'Не загружается информационная система, куда обращаться?',
                         'Удалённый доступ не подключается, куда писать?'):
            with self.subTest(question=question):
                page, cells = self.ask(question)
                self.assertIn(CHANNEL, page)
                self.assertEqual(cells, [])
                self.assertNotIn('подрядчиком', page)

    def test_non_it_fault_gets_no_budget_line(self):
        page, cells = self.ask('Не работает лифт, куда сообщить?')
        self.assertEqual(cells, [])
        self.assertNotIn('подрядчиком', page)
        self.assertNotIn(CHANNEL, page)   # the IT regulation is not about lifts
        self.assertIn('Недостаточно данных', page)


if __name__ == '__main__':
    unittest.main()
