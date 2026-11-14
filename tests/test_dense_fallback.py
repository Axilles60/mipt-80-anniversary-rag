"""Dense retrieval is optional: without a model the order must stay BM25."""
from contextlib import closing
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app import dense
from app.core import connect
from app.importer import init
from app.retrieval import imported


class DenseFallbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db = connect(str(Path(self.tmp.name) / 'db')); self.addCleanup(self.db.close)
        init(self.db)
        for n, text in enumerate(['Заявку на пропуск подают в службу безопасности.',
                                  'Пропуск выдают в день обращения.', 'Столовая работает с 12 часов.']):
            fid = f'S@v#p/{n}'
            self.db.execute('INSERT INTO import_fragments VALUES (?,?,?,?,?,?)', (fid, 'S', 'v', f'p/{n}', text, '{}'))
            self.db.execute('INSERT INTO import_fts VALUES (?,?)', (fid, text))
        self.db.commit()

    def test_missing_model_keeps_bm25_order(self):
        with mock.patch.dict(os.environ, {'NAVIGATOR_RETRIEVAL': 'bm25'}):
            expected = [r['id'] for r in imported(self.db, 'Как получить пропуск?', k=5)]
        missing = str(Path(self.tmp.name) / 'no-model')
        with mock.patch.dict(os.environ, {'NAVIGATOR_RETRIEVAL': 'hybrid', 'NAVIGATOR_EMBED_MODEL': missing}):
            got = [r['id'] for r in imported(self.db, 'Как получить пропуск?', k=5)]
            state = dense.status()
        self.assertEqual(got, expected)
        self.assertEqual(state['mode'], 'bm25')
        self.assertEqual(state['requested'], 'hybrid')
        self.assertIn('not found', state['fallback'])

    def test_unknown_mode_is_bm25(self):
        with mock.patch.dict(os.environ, {'NAVIGATOR_RETRIEVAL': 'magic'}):
            self.assertEqual(dense.mode(), 'bm25')

    def test_fusion_rewards_agreement(self):
        order = dense.fuse(['a', 'b', 'c'], ['c', 'd'])
        self.assertEqual(order[0], 'c')        # in both lists
        self.assertEqual(set(order), {'a', 'b', 'c', 'd'})

    def test_chunks_respect_limit(self):
        text = ('Предложение номер один. ' * 200) + '\n' + 'x' * 2500
        parts = dense.chunks(text)
        self.assertTrue(all(len(p) <= dense.CHUNK_CHARS for p in parts))
        self.assertEqual(''.join(parts).replace(' ', ''), text.replace(' ', '').replace('\n', ''))
        self.assertEqual(dense.chunks('   '), [])


if __name__ == '__main__':
    unittest.main()
