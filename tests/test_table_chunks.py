"""A DOCX table row reaches the generator as «column: value», like the index sees it."""
from contextlib import closing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'eval'))
from measure_rag import docx_table  # noqa: E402


class TableChunkTest(unittest.TestCase):
    def test_docx_row_text_has_column_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'manifest.json').write_text('[]')
            path = docx_table(root / 'retention.docx', ['ПЕРЕЧЕНЬ сроков хранения (учебный вымышленный документ)'],
                              [['Вид документа', 'Срок хранения'], ['Служебные записки', '5 лет'], ['Журналы посетителей', '3 года']])
            env = dict(os.environ, NAVIGATOR_DB=str(root / 'db'))
            subprocess.run([sys.executable, '-m', 'app', 'import', str(path), '--manifest', str(root / 'manifest.json')],
                           env=env, cwd=ROOT, check=True, capture_output=True)
            from app.core import connect
            with closing(connect(str(root / 'db'))) as db:
                texts = [r['text'] for r in db.execute("SELECT text FROM chunks WHERE level=2 AND meta LIKE '%table_row%'")]
        self.assertIn('Вид документа: Служебные записки; Срок хранения: 5 лет', texts)


if __name__ == '__main__':
    unittest.main()
