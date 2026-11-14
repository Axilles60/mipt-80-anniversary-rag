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


class NameTest(unittest.TestCase):
    ROWS = [{'id': 1, 'text': 'Код: 10000; Подразделение: Дирекция по науке; Уровень: 1; Финансодержатель: Орлова Н.В.'}]

    def test_substituted_name_is_dropped(self):
        raw = {'items': [{'text': 'Финансодержатель Дирекции по информационным технологиям — Орлова Н.В.', 'sources': [1]}]}
        kept, dropped, _ = rag.verify(raw, self.ROWS)
        self.assertEqual(kept, [])
        self.assertIn('имя', dropped[0]['why'])

    def test_named_entity_present_is_kept(self):
        raw = {'items': [{'text': 'Финансодержатель Дирекции по науке — Орлова Н.В.', 'sources': [1]}]}
        kept, _, _ = rag.verify(raw, self.ROWS)
        self.assertEqual(len(kept), 1)

    def test_numbers_from_heading_do_not_count(self):
        chunk = {'id': 3, 'text': '2. Настоящий приказ вводится в действие с 01.01.2025.',
                 'meta': {'document': 'ПРИКАЗ от 15.12.2024 № 610-2'}}
        raw = {'items': [{'text': 'Приказ № 610-2 вводится в действие с 01.01.2025.', 'sources': [1]}]}
        kept, _, _ = rag.verify(raw, [chunk])
        self.assertEqual(kept, [])

    def test_document_title_counts_as_support(self):
        chunk = {'id': 2, 'text': '1.1. Работник оформляет заявку на командировку по форме КМ-7.',
                 'meta': {'document': 'ПОЛОЖЕНИЕ о служебных командировках', 'section': '1. Оформление до поездки'}}
        raw = {'items': [{'text': 'По Положению о служебных командировках заявка оформляется по форме КМ-7.', 'sources': [1]}]}
        kept, _, _ = rag.verify(raw, [chunk])
        self.assertEqual(len(kept), 1)


class ConfidenceTest(unittest.TestCase):
    def test_without_reranker_always_confident(self):
        self.assertTrue(rag.confident([{'id': 1}]))

    def test_low_best_score_abstains(self):
        self.assertFalse(rag.confident([{'rerank': 0.0005}, {'rerank': 0.001}]))
        self.assertTrue(rag.confident([{'rerank': 0.0005}, {'rerank': 0.4}]))


class TopicModel:
    def __init__(self, reply): self.reply, self.calls = reply, 0
    def complete(self, messages, schema=None, max_tokens=None):
        self.calls += 1
        if isinstance(self.reply, Exception): raise self.reply
        return self.reply


class TopicTest(unittest.TestCase):
    def test_off_topic_when_model_says_so(self):
        self.assertEqual(rag.topic(TopicModel('{"topic": "off_topic"}'), 'Посоветуй рецепт борща.'), 'off_topic')

    def test_work_question_without_fragments_is_no_data(self):
        self.assertEqual(rag.topic(TopicModel('{"topic": "work"}'), 'Как оформить больничный?'), 'insufficient')

    def test_no_words_is_not_classified(self):
        model = TopicModel('{"topic": "off_topic"}')
        self.assertEqual(rag.topic(model, '???'), 'insufficient')
        self.assertEqual(model.calls, 0)

    def test_failure_is_no_data(self):
        self.assertEqual(rag.topic(TopicModel(OSError('down')), 'Расскажи анекдот'), 'insufficient')


if __name__ == '__main__':
    unittest.main()
