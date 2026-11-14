"""DOCX tables as organisations write them (fictional data): a title row over the whole
width, an empty row, the header, column numbers, a hierarchy written as an outline
(the parent once, children with its column empty), vertical merges, one-row tables."""
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from xml.sax.saxutils import escape
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import chunking  # noqa: E402
from app.importer import batch  # noqa: E402

NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def cell(text='', merge=None, span=None):
    pr = ''
    if span: pr += f'<w:gridSpan w:val="{span}"/>'
    if merge == 'restart': pr += '<w:vMerge w:val="restart"/>'
    elif merge == 'continue': pr += '<w:vMerge/>'
    return f'<w:tc>{"<w:tcPr>" + pr + "</w:tcPr>" if pr else ""}<w:p><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p></w:tc>'


def table(rows, columns):
    grid = '<w:tblGrid>' + '<w:gridCol/>' * columns + '</w:tblGrid>'
    return '<w:tbl>' + grid + ''.join('<w:tr>' + ''.join(r) + '</w:tr>' for r in rows) + '</w:tbl>'


def write_docx(path):
    body = '<w:p><w:r><w:t>ПРИКАЗ</w:t></w:r></w:p>'
    body += table([[cell('от 01.02.2030'), cell('№ 77-7')]], 2)
    body += '<w:p><w:r><w:t>Об утверждении учебной структуры (вымышленный документ)</w:t></w:r></w:p>'
    body += table([
        [cell('Учебная структура подразделений на 2030 год', span=4), cell('')],
        [cell(''), cell(''), cell(''), cell(''), cell('')],
        [cell('Ответственный'), cell('Уровень 1'), cell('Уровень 2'), cell('Уровень 3'), cell('Уровень 4')],
        [cell('1'), cell('2'), cell('3'), cell('4'), cell('5')],
        [cell('Зайцев А.Б.'), cell(''), cell(''), cell(''), cell('')],
        [cell(''), cell('101 отдел снабжения'), cell(''), cell(''), cell('')],
        [cell(''), cell('102 управление связи', merge='restart'), cell('201 отдел сетей'), cell(''), cell('')],
        [cell(''), cell('', merge='continue'), cell('202 отдел телефонии'), cell(''), cell('')],
        [cell(''), cell('103 учебный центр'), cell('203 класс языков'), cell(''), cell('')],
        [cell(''), cell(''), cell('204 класс черчения'), cell(''), cell('')],
        [cell('Лисицына В.Г.'), cell(''), cell(''), cell(''), cell('')],
        [cell(''), cell('104 архив'), cell(''), cell(''), cell('')],
    ], 5)
    xml = f'<w:document xmlns:w="{NS}"><w:body>{body}</w:body></w:document>'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('word/document.xml', xml)
    return path


class OutlineTableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        path = write_docx(Path(cls.tmp.name) / 'structure.docx')
        db_path = Path(cls.tmp.name) / 'db'
        batch(str(db_path), [str(path)])
        with closing(sqlite3.connect(db_path)) as db:
            db.row_factory = sqlite3.Row
            key = db.execute('SELECT source_id, version FROM imports').fetchone()
            cls.chunks = [c for c in chunking.build(db, *key) if c['level'] == 2]
        cls.rows = [c['text'] for c in cls.chunks if c['meta'].get('type') == 'table_row']

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_row_carries_its_whole_path(self):
        self.assertIn('Ответственный: Зайцев А.Б.; Уровень 1: 101 отдел снабжения', self.rows)
        self.assertIn('Ответственный: Зайцев А.Б.; Уровень 1: 103 учебный центр; Уровень 2: 204 класс черчения', self.rows)
        self.assertIn('Ответственный: Лисицына В.Г.; Уровень 1: 104 архив', self.rows)

    def test_vertical_merge_takes_the_value_above(self):
        self.assertIn('Ответственный: Зайцев А.Б.; Уровень 1: 102 управление связи; Уровень 2: 202 отдел телефонии', self.rows)

    def test_title_empty_numbers_and_group_rows_are_not_rows(self):
        joined = '\n'.join(self.rows)
        self.assertNotIn('Ответственный: 1', joined)
        self.assertFalse(any(r == 'Ответственный: Зайцев А.Б.' for r in self.rows))
        self.assertFalse(any(r.startswith('Учебная структура') for r in self.rows))
        caption = [c['meta'].get('caption') for c in self.chunks if c['meta'].get('caption')]
        self.assertTrue(caption and caption[0].startswith('Учебная структура подразделений на 2030 год'))

    def test_one_row_table_is_kept(self):
        self.assertIn('от 01.02.2030; № 77-7', self.rows)

    def test_caption_is_searched_with_the_rows(self):
        row = next(c for c in self.chunks if c['text'].endswith('104 архив'))
        self.assertIn('Учебная структура подразделений на 2030 год', row['index_text'])


class OutlineSheetTest(unittest.TestCase):
    """The same outline in XLSX: the holder of a row is the nearest name above it in the holder column."""
    def test_holder_is_the_nearest_name_above(self):
        import openpyxl
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            book = openpyxl.Workbook(); sheet = book.active; sheet.title = 'Структура'
            for row in [('Ответственный', 'Уровень 1', 'Уровень 2'),
                        ('Зайцев А.Б.', None, None),
                        (None, '101 отдел снабжения', None),
                        (None, '102 управление связи', '201 отдел сетей'),
                        (None, None, '202 отдел телефонии'),
                        ('Лисицына В.Г.', None, None),
                        (None, '104 архив', None),
                        (None, '105 канцелярия', None)]:
                sheet.append(row)
            path = Path(tmp) / 'structure.xlsx'; book.save(path)
            db_path = Path(tmp) / 'db'
            batch(str(db_path), [str(path)])
            with closing(sqlite3.connect(db_path)) as db:
                db.row_factory = sqlite3.Row
                key = db.execute('SELECT source_id, version FROM imports').fetchone()
                rows = [c['text'] for c in chunking.build(db, *key) if c['level'] == 2]
        self.assertIn('Ответственный: Зайцев А.Б.; Уровень 1: 101 отдел снабжения', rows)
        self.assertIn('Ответственный: Зайцев А.Б.; Уровень 1: 102 управление связи; Уровень 2: 202 отдел телефонии', rows)
        self.assertIn('Ответственный: Лисицына В.Г.; Уровень 1: 105 канцелярия', rows)
        self.assertFalse(any(r == 'Ответственный: Зайцев А.Б.' for r in rows))


if __name__ == '__main__':
    unittest.main()
