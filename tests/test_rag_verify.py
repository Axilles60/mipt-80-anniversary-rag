"""Claim verification: a wrong fragment number is repaired, an unsupported claim is dropped."""
import unittest

from app import rag

CHUNKS = [{'id': 1, 'text': '2.1. Заявку на закупку подписывает финансодержатель подразделения по перечню финансовой структуры.'},
          {'id': 2, 'text': 'Код: 10100; Подразделение: Лаборатория прикладной оптики; Уровень: 2; Финансодержатель: Орлова Н.В.'},
          {'id': 3, 'text': '1. Рабочий день начинается в 9:00 и заканчивается в 18:00.'}]


class VerifyTest(unittest.TestCase):
    def test_wrong_number_is_repaired(self):
        raw = {'items': [{'text': 'Финансодержатель Лаборатории прикладной оптики — Орлова Н.В.', 'sources': [3]}]}
        kept, dropped, _ = rag.verify(raw, CHUNKS)
        self.assertEqual(dropped, [])
        self.assertEqual([c['id'] for c in kept[0]['chunks']], [2])

    def test_rule_and_row_are_joined(self):
        raw = {'items': [{'text': 'Заявку на закупку для Лаборатории прикладной оптики подписывает финансодержатель Орлова Н.В.',
                          'sources': [1]}]}
        kept, dropped, _ = rag.verify(raw, CHUNKS)
        self.assertEqual(dropped, [])
        self.assertEqual(sorted(c['id'] for c in kept[0]['chunks']), [1, 2])

    def test_unsupported_number_is_dropped(self):
        raw = {'items': [{'text': 'Рабочий день заканчивается в 19:00.', 'sources': [3]}]}
        kept, dropped, _ = rag.verify(raw, CHUNKS)
        self.assertEqual(kept, [])
        self.assertEqual(len(dropped), 1)

    def test_claim_without_citation_can_be_attributed(self):
        raw = {'items': [{'text': 'Рабочий день начинается в 9:00.', 'sources': []}]}
        kept, _, _ = rag.verify(raw, CHUNKS)
        self.assertEqual([c['id'] for c in kept[0]['chunks']], [3])


if __name__ == '__main__':
    unittest.main()
