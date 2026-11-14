"""The RAG context marks a document amended by an act that is also in the context."""
from contextlib import closing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'eval'))
from synthetic_docs import write_docx  # noqa: E402

BASE = [('Title', 'ПОЛОЖЕНИЕ о дистанционной работе (учебный вымышленный документ)'),
        'Утверждено приказом от 01.02.2024 № 120-1, вводится в действие с 01.03.2024.',
        '1. Работник может работать дистанционно не более 2 дней в неделю.']
CHANGE = ['ПРИКАЗ от 15.12.2024 № 610-2',
          'О внесении изменений в положение о дистанционной работе работников (учебный вымышленный документ)',
          '1. Внести изменения в положение о дистанционной работе, утверждённое приказом от 01.02.2024 № 120-1: '
          'в пункте 1 слова «не более 2 дней в неделю» заменить словами «не более 3 дней в неделю».',
          '2. Настоящий приказ вводится в действие с 01.01.2025.']


class AmendHintTest(unittest.TestCase):
    def test_amended_document_is_marked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'manifest.json').write_text('[]')
            paths = [write_docx(root / 'remote.docx', BASE), write_docx(root / 'change.docx', CHANGE)]
            env = dict(os.environ, NAVIGATOR_DB=str(root / 'db'))
            env.pop('NAVIGATOR_RETRIEVAL', None)
            subprocess.run([sys.executable, '-m', 'app', 'import', *map(str, paths), '--manifest', str(root / 'manifest.json')],
                           env=env, cwd=ROOT, check=True, capture_output=True)
            from app.core import connect
            from app import rag
            with closing(connect(str(root / 'db'))) as db:
                chunks, headers = rag.build_context(db, 'Сколько дней в неделю можно работать дистанционно?')
            marked = [h for h in headers.values() if 'изменён приказом № 610-2 с 01.01.2025' in h]
            self.assertEqual(len(marked), 1)
            self.assertIn('120-1', marked[0])


if __name__ == '__main__':
    unittest.main()
