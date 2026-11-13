"""RAG chunks (stage R1) built at the standard import of fictional documents."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'eval'))
from synthetic_docs import write_docx, write_pdf, write_xlsx  # noqa: E402

LONG = '4.2. ' + ' '.join(f'Подпункт {n}: работник выполняет действие номер {n} в установленном порядке и сообщает о результате.'
                         for n in range(1, 40))


class ChunkingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(); root = Path(cls.tmp.name)
        files = [
            write_docx(root / 'reg.docx', [('Title', 'ПОЛОЖЕНИЕ о порядке (вымышленное)'), ('Heading1', '4. Порядок действий'),
                                           '4.1. Работник подаёт заявку по форме АБ-1.', LONG]),
            write_docx(root / 'form.docx', ['ФОРМА заявки', 'ФИО: ____________ Подпись ________']),
            write_pdf(root / 'p.pdf', [['РЕГЛАМЕНТ проверки (вымышленный)', '1. Общие положения', '1.1. Проверка проводится ежегодно.',
                                        '1.2. Результаты оформляются актом.']]),
            write_xlsx(root / 's.xlsx', [['Код', 'Название', 'Уровень'], [100, 'Дирекция', 1], [110, 'Отдел учёта', 2]]),
        ]
        cls.db = str(root / 'db')
        env = dict(os.environ, NAVIGATOR_DB=cls.db)
        (root / 'm.json').write_text('[]')
        subprocess.run([sys.executable, '-m', 'app', 'import', *map(str, files), '--manifest', str(root / 'm.json')],
                       cwd=ROOT, env=env, check=True, capture_output=True)
        from app.core import connect
        cls.con = connect(cls.db)
        cls.chunks = [dict(r, meta=json.loads(r['meta'])) for r in cls.con.execute('SELECT * FROM chunks ORDER BY source_id, ord')]

    @classmethod
    def tearDownClass(cls):
        cls.con.close(); cls.tmp.cleanup()

    def children(self):
        return [c for c in self.chunks if c['level'] == 2]

    def test_addresses_are_exact(self):
        for c in self.children():
            if c['start'] is None: continue
            fragment = self.con.execute('SELECT text FROM import_fragments WHERE id=?', (c['fragment_id'],)).fetchone()['text']
            self.assertEqual(' '.join(fragment[c['start']:c['end']].split()), c['text'])

    def test_items_sections_and_titles(self):
        item = next(c for c in self.children() if c['text'].startswith('4.1.'))
        self.assertEqual(item['meta']['item'], '4.1')
        self.assertEqual(item['meta']['section'], '4. Порядок действий')
        self.assertIn('ПОЛОЖЕНИЕ о порядке', item['index_text'])          # document title in the index text
        parent = next(c for c in self.chunks if c['id'] == item['parent_id'])
        self.assertIn('4.1. Работник подаёт заявку', parent['text'])
        pdf_item = next(c for c in self.children() if c['text'].startswith('1.2.'))
        self.assertEqual(pdf_item['meta']['section'], '1. Общие положения')

    def test_long_item_split_within_limit_with_overlap(self):
        from app.chunking import MAX_TOKENS, tokens
        parts = [c for c in self.children() if 'Подпункт' in c['text']]
        self.assertGreater(len(parts), 1)
        for c in parts:
            self.assertLessEqual(tokens(c['text']), MAX_TOKENS)
            self.assertLess(tokens(c['index_text']), 500)                  # embedder limit 512 incl. prefix
        self.assertIn(parts[0]['text'].split()[-1], parts[1]['index_text'])  # overlap from the previous piece

    def test_form_and_sheet_rows(self):
        form = [c for c in self.children() if 'Подпись' in c['text']]
        self.assertEqual(form[0]['meta']['kind'], 'form')
        self.assertEqual(next(c for c in self.children() if c['text'].startswith('4.1.'))['meta']['kind'], 'norm')
        row = next(c for c in self.children() if c['meta'].get('type') == 'sheet_row' and 'Отдел учёта' in c['text'])
        self.assertIn('Дирекция ›', row['index_text'])                     # parent row path
        self.assertIn('Название: Отдел учёта', row['text'])


if __name__ == '__main__':
    unittest.main()
