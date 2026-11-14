"""A document title or section heading is not an answer to a question.

Fictional DOCX documents through the standard import and POST /ask. A question
whose words match only a title must not be answered with that title; a question
answered by a paragraph keeps its answer.
"""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'eval'))
from synthetic_docs import write_docx  # noqa: E402
from web_harness import Session  # noqa: E402

TITLE_STYLE = [('Title', 'Порядок допуска посетителей в административное здание (вымышленный)'),
               'Разовый допуск посетителя оформляет служба безопасности по заявке принимающего работника.',
               'Принимающий работник сопровождает посетителя на всё время визита.']
PLAIN_TITLE = ['ПОЛОЖЕНИЕ о служебном жилье (вымышленное)',
               'Комната в общежитии предоставляется по решению комиссии организации.',
               'Решение комиссии оформляется протоколом в течение десяти рабочих дней.']
NUMBERED = ['Инструкция по работе архива (вымышленная)',
            '1. Хранение личных дел сотрудников',
            'Архив принимает документы по описи от отдела кадров.',
            '2. Выдача копий',
            'Копии выдаются по письменному запросу работника в течение трёх рабочих дней.']


def build(root):
    return [write_docx(root / 'visitors.docx', TITLE_STYLE), write_docx(root / 'housing.docx', PLAIN_TITLE),
            write_docx(root / 'archive.docx', NUMBERED)]


class HeadingAnswerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(build)
        cls.text = {f['id']: f['text'] for f in cls.session.fragments()}

    @classmethod
    def tearDownClass(cls):
        cls.session.close()

    def cited(self, question):
        page, items = self.session.ask(question)
        return page, [self.text[i] for item in items for i in item['links'] if i in self.text]

    def test_title_style_is_not_an_answer(self):
        _, cited = self.cited('Как провести гостя в административное здание?')
        self.assertFalse([t for t in cited if t.startswith('Порядок допуска')], cited)

    def test_plain_first_paragraph_title_is_not_an_answer(self):
        _, cited = self.cited('Как получить служебное жильё?')
        self.assertFalse([t for t in cited if t.startswith('ПОЛОЖЕНИЕ')], cited)

    def test_numbered_section_heading_is_not_an_answer(self):
        _, cited = self.cited('Где хранятся личные дела сотрудников?')
        self.assertNotIn('1. Хранение личных дел сотрудников', cited)

    def test_content_answer_kept(self):
        page, cited = self.cited('Кто сопровождает посетителя?')
        self.assertIn('Принимающий работник сопровождает посетителя на всё время визита.', cited)
        _, cited = self.cited('В какой срок выдаются копии по запросу работника?')
        self.assertTrue(any('Копии выдаются' in t for t in cited), cited)


if __name__ == '__main__':
    unittest.main()
