"""Evaluator eval3 (eval/measure_rag.py): what counts as a statement of the answer.

The cases follow the external audit of commit d23f0fc (probes E01–E08): facts
found only in «Источник» or in the «no data for part» line, an unsupported short
answer or claim, a statement instead of a refusal, nDCG above 1.
"""
from pathlib import Path
import sqlite3
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'eval')]
import compare_runs  # noqa: E402
import measure_rag as m  # noqa: E402

RATE = 'FILE-aaaa@1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a#body/1/paragraph'
SIGN = 'FILE-bbbb@2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b#body/2/paragraph'
PERSON = 'FILE-cccc@3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c#sheet/S/cell/A4'


def db():
    d = sqlite3.connect(':memory:'); d.row_factory = sqlite3.Row
    d.execute('CREATE TABLE import_fragments(id TEXT, text TEXT)')
    d.executemany('INSERT INTO import_fragments VALUES (?, ?)', [
        (RATE, 'Суточные составляют 700 условных единиц.'),
        (SIGN, 'Заявку подписывает финансодержатель подразделения.'),
        (PERSON, 'Финансодержатель — Седов К.А.')])
    return d


NEEDLES = {'RATE': 'суточные составляют 700 условных единиц.', 'SIGN': 'заявку подписывает финансодержатель подразделения.',
           'PERSON': 'финансодержатель — седов к.а.'}
CASE = {'id': 'E', 'type': 'fact', 'question': 'Какие суточные?', 'expected': ['RATE'], 'facts': ['700'],
        'forbidden': [], 'expect': 'answer'}


def link(fid):
    return f'<a href="/fragment?id={fid.replace("@", "%40").replace("#", "%23")}">{fid}</a>'


def score(body, **changes):
    return m.score(dict(CASE, **changes), '<form></form>' + body, 1.0, db(), NEEDLES)


class StatementTest(unittest.TestCase):
    def test_supported_answer_is_correct(self):
        r = score(f'<h2>Ответ</h2><ol><li>Суточные составляют 700 условных единиц. {link(RATE)}</li></ol>')
        self.assertEqual(r['verdict'], 'correct')

    def test_fact_only_in_source_line_does_not_count(self):
        r = score(f'<ol><li>Размер суточных установлен. {link(RATE)}</li></ol><p>Источник: Приказ № 700.</p>')
        self.assertEqual(r['verdict'], 'incomplete')

    def test_fact_only_in_gap_does_not_count(self):
        r = score(f'<ol><li>Заявку подписывает финансодержатель подразделения. {link(SIGN)}</li></ol>'
                  '<p>Недостаточно данных для части вопроса: подтверждение того, что это Седов.</p>',
                  expected=['SIGN', 'PERSON'], facts=['Седов'])
        self.assertEqual(r['verdict'], 'incomplete')

    def test_unsupported_short_answer_is_false(self):
        r = score(f'<h3>Суточные составляют 9000.</h3><ol><li>Суточные составляют 700 условных единиц. {link(RATE)}</li></ol>')
        self.assertEqual(r['verdict'], 'false_answer')
        self.assertTrue(r['unsupported'][0]['short'])

    def test_unsupported_claim_is_false(self):
        r = score(f'<ol><li>Суточные составляют 700 условных единиц, премия — 9000. {link(RATE)}</li></ol>')
        self.assertEqual(r['verdict'], 'false_answer')

    def test_denying_an_existing_fact_is_false(self):
        r = score(f'<h3>Размер суточных в документах не указан.</h3><ol><li>Заявку подписывает финансодержатель. {link(SIGN)}</li></ol>'
                  '<p>Недостаточно данных для части вопроса: суточные 700.</p>', expected=['RATE', 'SIGN'])
        self.assertEqual(r['verdict'], 'false_answer')

    def test_fact_in_another_case_counts(self):
        self.assertTrue(__import__('re').search(m.fact_pattern('директор по информационным технологиям'),
                                                'согласуется директором по информационным технологиям'))
        self.assertEqual(m.fact_pattern('3 рабочих дней'), r'3 рабо\w* дней')
        self.assertEqual(m.fact_pattern('Приложени\\w* 1|заявлени'), 'Приложени\\w* 1|заявлени')

    def test_forbidden_named_only_as_missing_is_not_asserted(self):
        r = score(f'<ol><li>Суточные составляют 700 условных единиц. {link(RATE)}</li></ol>'
                  '<p>Недостаточно данных для части вопроса: сумма 1000.</p>', forbidden=['1000'])
        self.assertEqual(r['verdict'], 'correct')


class RefusalTest(unittest.TestCase):
    NONE = {'expected': [], 'facts': [], 'expect': 'insufficient', 'forbidden': ['99999']}

    def test_refusal_is_correct(self):
        self.assertEqual(score('<p>Недостаточно данных для ответа в доступных материалах.</p>', **self.NONE)['verdict'], 'correct')

    def test_statement_without_citation_is_not_a_refusal(self):
        self.assertEqual(score('<p>Суточные составляют 99999.</p>', **self.NONE)['verdict'], 'false_answer')

    def test_empty_page_is_not_a_refusal(self):
        self.assertEqual(score('', **self.NONE)['verdict'], 'no_refusal')

    def test_off_topic_message(self):
        page = '<p>Вопрос не относится к внутренним документам и регламентам организации; навигатор отвечает только по ним.</p>'
        self.assertEqual(score(page, expected=[], facts=[], expect='off_topic')['verdict'], 'correct')


class SummaryTest(unittest.TestCase):
    def test_gap_with_tokens_is_counted(self):
        r = score(f'<ol><li>Суточные составляют 700 условных единиц. {link(RATE)}</li></ol>'
                  '<p>Недостаточно данных для части вопроса: Выплата премии 99999 обязательна.</p>')
        self.assertEqual(m.summarize([CASE], [r])['gap_with_tokens'], 1)

    def test_ndcg_stays_within_one(self):
        out = m.ranking([CASE], {'E': ['a', 'b', 'c']}, {'a': ['RATE'], 'b': ['RATE'], 'c': ['RATE']})
        self.assertEqual(out['ndcg@5'], 1.0)

    def test_fragment_of_another_build_is_found_by_locator(self):
        self.assertEqual(m.unit_text(db(), 'FILE-aaaa@9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f#body/1/paragraph'), 'Суточные составляют 700 условных единиц.')

    def test_gold_hash_follows_labels(self):
        self.assertNotEqual(m.gold_key(CASE), m.gold_key(dict(CASE, facts=['800'])))


class CompareTest(unittest.TestCase):
    def test_questions_with_other_labels_are_left_out(self):
        a = {'X': {'gold': '1'}, 'Y': {'gold': '2'}, 'Z': {'gold': '3'}}
        b = {'X': {'gold': '1'}, 'Y': {'gold': '9'}}
        self.assertEqual(compare_runs.pairs(a, b), (['X'], ['Z'], ['Y']))

    def test_old_reports_compare_question_and_status(self):
        a = {'V39': {'question': 'q', 'expect': 'insufficient'}}
        b = {'V39': {'question': 'q', 'expect': 'partial'}}
        self.assertEqual(compare_runs.pairs(a, b), ([], [], ['V39']))


if __name__ == '__main__':
    unittest.main()
