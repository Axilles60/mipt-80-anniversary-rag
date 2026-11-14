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

    def test_number_inside_longer_number_is_not_support(self):
        raw = {'items': [{'text': 'Код Лаборатории прикладной оптики — 10.', 'sources': [2]}]}
        kept, _, _ = rag.verify(raw, CHUNKS)
        self.assertEqual(kept, [])

    def test_number_with_another_unit_is_not_support(self):
        chunks = [{'id': 4, 'text': '1.2. Закупка стоимостью от 100 до 600 тысяч условных единиц проводится запросом котировок не менее чем у 3 поставщиков.'},
                  {'id': 5, 'text': '2.1. Суточные выплачиваются в размере 700 условных единиц за каждый день командировки.'}]
        raw = {'items': [{'text': 'Закупка стоимостью 700 тысяч условных единиц проводится запросом котировок.', 'sources': [1, 2]}]}
        kept, _, _ = rag.verify(raw, chunks)
        self.assertEqual(kept, [])

    def test_unit_may_follow_a_qualifier(self):
        chunks = [{'id': 6, 'text': '3.1. Работник сдаёт авансовый отчёт по форме КМ-9 в бухгалтерию в течение 3 рабочих дней со дня возвращения.'}]
        raw = {'items': [{'text': 'Авансовый отчёт сдают в бухгалтерию в течение 3 дней со дня возвращения.', 'sources': [1]}]}
        kept, _, _ = rag.verify(raw, chunks)
        self.assertEqual(len(kept), 1)

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

    def test_requisites_from_heading_count(self):
        # The reader sees the document's requisites in «Источник».
        chunk = {'id': 3, 'text': '2. Настоящий приказ вводится в действие с 01.01.2025.',
                 'header': 'Приказ № 610-2 от 15.12.2024', 'meta': {'document': 'О внесении изменений'}}
        raw = {'items': [{'text': 'Приказ № 610-2 от 15.12.2024 вводится в действие с 01.01.2025.', 'sources': [1]}]}
        kept, _, _ = rag.verify(raw, [chunk])
        self.assertEqual(len(kept), 1)

    def test_amount_from_heading_does_not_count(self):
        chunk = {'id': 3, 'text': '3.1. Первичная обработка обращения выполняется в течение 8 рабочих часов.',
                 'meta': {'document': 'Регламент', 'section': '3 рабочих дня на ответ'}}
        raw = {'items': [{'text': 'Первичная обработка обращения выполняется в течение 3 рабочих дней.', 'sources': [1]}]}
        kept, _, _ = rag.verify(raw, [chunk])
        self.assertEqual(kept, [])

    def test_trailing_dot_of_address(self):
        chunk = {'id': 4, 'text': '2.2. Подписанная заявка направляется в отдел закупок по адресу zakupki@example.invalid.'}
        raw = {'items': [{'text': 'Заявку направляют в отдел закупок по адресу zakupki@example.invalid.', 'sources': [1]}]}
        kept, _, _ = rag.verify(raw, [chunk])
        self.assertEqual(len(kept), 1)

    def test_fragment_references_are_not_facts(self):
        raw = {'items': [{'text': 'Рабочий день начинается в 9:00 (фрагмент 4).', 'sources': [3]},
                         {'text': '<<<[2] structure.xlsx Финансодержатель: Орлова Н.В.', 'sources': [2]}]}
        kept, dropped, _ = rag.verify(raw, CHUNKS)
        self.assertEqual(dropped, [])
        self.assertEqual([k['text'] for k in kept], ['Рабочий день начинается в 9:00.', 'Финансодержатель: Орлова Н.В.'])

    def test_absence_statement_is_not_a_cited_fact(self):
        raw = {'items': [{'text': 'В документах не указан номер телефона охраны.', 'sources': [3]},
                         {'text': 'Рабочий день начинается в 9:00.', 'sources': [3]}]}
        kept, dropped, _ = rag.verify(raw, CHUNKS)
        self.assertEqual([k['text'] for k in kept], ['Рабочий день начинается в 9:00.'])
        self.assertEqual(dropped, [])

    def test_bracket_left_after_file_name_is_closed(self):
        self.assertEqual(rag.clean_claim('Срок — 5 рабочих дней (памятка слушателю, training-memo.pdf).'),
                         'Срок — 5 рабочих дней (памятка слушателю).')

    def test_point_numbers_are_kept(self):
        self.assertEqual(rag.clean_claim('Срок — 3 рабочих дня согласно пункту 3.1.'), 'Срок — 3 рабочих дня согласно пункту 3.1.')

    def test_supported_short_answer_rescues_failed_items(self):
        raw = {'short_answer': 'Рабочий день заканчивается в 18:00.',
               'items': [{'text': 'См. раздел 7 и фрагмент 12: конец дня в 19:00.', 'sources': [3]}]}
        kept, _, _ = rag.verify(raw, CHUNKS)
        self.assertEqual([k['text'] for k in kept], ['Рабочий день заканчивается в 18:00.'])
        self.assertEqual([c['id'] for c in kept[0]['chunks']], [3])

    def test_document_title_counts_as_support(self):
        chunk = {'id': 2, 'text': '1.1. Работник оформляет заявку на командировку по форме КМ-7.',
                 'meta': {'document': 'ПОЛОЖЕНИЕ о служебных командировках', 'section': '1. Оформление до поездки'}}
        raw = {'items': [{'text': 'По Положению о служебных командировках заявка оформляется по форме КМ-7.', 'sources': [1]}]}
        kept, _, _ = rag.verify(raw, [chunk])
        self.assertEqual(len(kept), 1)


def frag(source, text):
    return {'source_id': source, 'version': '1', 'text': text, 'meta': {}}


class ConflictTest(unittest.TestCase):
    RULE = frag('A', '2.1. Копию удостоверения о повышении квалификации работник сдаёт в отдел кадров в течение 10 рабочих дней после окончания обучения.')
    MEMO = frag('B', '1. Копию удостоверения сдайте в отдел кадров в течение 5 рабочих дней после окончания курса.')

    def test_same_subject_different_amounts(self):
        found = rag.conflicts([self.RULE, self.MEMO], 'В какой срок сдать копию удостоверения?')
        self.assertEqual(found, [(1, 2, '10 рабочих дней', '5 рабочих дней')])
        self.assertIn('Расхождение: фрагменты [1] и [2]', rag.prompt('В какой срок сдать копию удостоверения?',
                                                                    [self.RULE, self.MEMO], {}))

    def test_shared_deadline_words_do_not_make_a_subject(self):
        self.assertEqual(rag.conflicts([self.RULE, self.MEMO], 'Почему авансовый отчёт сдают за 10 рабочих дней?'), [])

    def test_absence_short_answer_is_not_rescued(self):
        chunk = frag('A', '2.2. В обращении указываются ФИО, кабинет, телефон, описание проблемы и инвентарный номер устройства.')
        kept, _, _ = rag.verify({'short_answer': 'В документах не указан номер телефона охраны.', 'items': []}, [chunk])
        self.assertEqual(kept, [])

    def test_question_about_something_else(self):
        self.assertEqual(rag.conflicts([self.RULE, self.MEMO], 'Что будет, если уволиться через полгода после обучения?'), [])

    def test_different_subjects(self):
        training = frag('A', '1.3. Обучение стоимостью свыше 50 тысяч условных единиц согласует комиссия по обучению.')
        purchase = frag('B', '1.3. Закупка стоимостью свыше 600 тысяч условных единиц проводится конкурсом.')
        self.assertEqual(rag.conflicts([training, purchase], 'Кто согласует обучение стоимостью свыше 50 тысяч?'), [])

    def test_amending_act_is_not_a_conflict(self):
        base = frag('A', '1. Работник может работать дистанционно не более 2 дней в неделю.')
        act = frag('B', '1. Внести изменения в положение о дистанционной работе: в пункте 1 слова «не более 2 дней в неделю» '
                        'заменить словами «не более 3 дней в неделю».')
        self.assertEqual(rag.conflicts([base, act], 'Сколько дней в неделю можно работать дистанционно?'), [])


@unittest.skipUnless(rag._morph(), 'pymorphy3 is not installed')
class SubjectTest(unittest.TestCase):
    HALL = {'id': 1, 'text': '4. КЗ вмещает не более 120 участников; при большем числе заявка не рассматривается.',
            'meta': {'document': 'ПОРЯДОК бронирования конференц-зала'}}
    SUP = {'id': 2, 'text': '4.1. СП работает по рабочим дням с 8:30 до 17:30, в пятницу — до 16:15.',
           'meta': {'document': 'РЕГЛАМЕНТ работы службы поддержки'}}

    def test_neighbouring_subject_is_dropped(self):
        kept, dropped, _ = rag.verify({'items': [{'text': 'Касса работает в пятницу до 16:15.', 'sources': [1]}]},
                                      [self.SUP], 'До скольки работает касса в пятницу?')
        self.assertEqual(kept, [])
        self.assertIn('предмет', dropped[0]['why'])

    def test_qualifier_of_the_subject_is_checked(self):
        kept, _, _ = rag.verify({'items': [{'text': 'Актовый зал вмещает не более 120 человек.', 'sources': [1]}]},
                                [self.HALL], 'Сколько человек вмещает актовый зал?')
        self.assertEqual(kept, [])

    def test_synonym_confirmed_by_judge_is_kept(self):
        form = {'id': 3, 'text': 'Приложение 1 к порядку выдачи пропусков. ФОРМА заявления на постоянный пропуск', 'meta': {}}
        raw = {'items': [{'text': 'Бланк заявления на постоянный пропуск — приложение 1 к порядку выдачи пропусков.', 'sources': [1]}]}
        asked = []
        judge = lambda q, w, f: asked.append(w) or True
        kept, _, _ = rag.verify(raw, [form], 'Где взять бланк заявления на пропуск?', judge=judge)
        self.assertEqual(len(kept), 1)
        self.assertEqual(asked, ['бланк'])
        kept, dropped, _ = rag.verify(raw, [form], 'Где взять бланк заявления на пропуск?', judge=lambda q, w, f: False)
        self.assertEqual(kept, [])

    def test_judge_not_asked_when_subject_is_covered(self):
        judge = lambda q, w, f: self.fail('judge must not be asked')
        kept, _, _ = rag.verify({'items': [{'text': 'Конференц-зал вмещает не более 120 человек.', 'sources': [1]}]},
                                [self.HALL], 'Сколько человек вмещает конференц-зал АХО?', judge=judge)
        self.assertEqual(len(kept), 1)

    def test_covered_subject_and_generic_words_pass(self):
        kept, _, _ = rag.verify({'items': [{'text': 'Конференц-зал вмещает не более 120 человек.', 'sources': [1]}]},
                                [self.HALL], 'Сколько человек вмещает конференц-зал АХО?')
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
