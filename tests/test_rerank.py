"""Reranking is optional: off or without a model the fused order stays."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app import rerank


def chunks():
    return [{'id': i, 'text': t, 'meta': {'document': 'Положение', 'section': 'Пропуска'}}
            for i, t in enumerate(['Столовая работает с 12 часов.', 'Пропуск выдают в день обращения.',
                                   'Заявку на пропуск подают в службу безопасности.'])]


class FakeEncoder:
    def scores(self, question, texts):
        texts = [t.splitlines()[-1] for t in texts]          # the chunk text, without the header line
        return [0.9 if 'пропуск' in t.lower() and 'заявк' in t.lower() else 0.5 if 'пропуск' in t.lower() else 0.1
                for t in texts]


class RerankTest(unittest.TestCase):
    def test_off_by_default_keeps_order(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('NAVIGATOR_RERANK', None)
            self.assertEqual([c['id'] for c in rerank.rerank('Как получить пропуск?', chunks())], [0, 1, 2])

    def test_missing_model_keeps_order_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {'NAVIGATOR_RERANK': '1', 'NAVIGATOR_RERANK_MODEL': str(Path(tmp) / 'none')}
            with mock.patch.dict(os.environ, env):
                got = [c['id'] for c in rerank.rerank('Как получить пропуск?', chunks())]
                state = rerank.status()
        self.assertEqual(got, [0, 1, 2])
        self.assertTrue(state.startswith('unavailable'))

    def test_reorders_head_only_and_scores(self):
        items = chunks() + [{'id': 3, 'text': 'Заявка на пропуск — форма 1.', 'meta': {}}]
        with mock.patch.dict(os.environ, {'NAVIGATOR_RERANK': '1', 'NAVIGATOR_RERANK_DEPTH': '3'}), \
                mock.patch.object(rerank, 'model', return_value=FakeEncoder()):
            got = rerank.rerank('Как получить пропуск?', items)
        self.assertEqual([c['id'] for c in got], [2, 1, 0, 3])   # the 4th is beyond depth: not scored, stays last
        self.assertEqual(got[0]['rerank'], 0.9)
        self.assertNotIn('rerank', got[3])

    def test_pair_text_has_document_and_section(self):
        self.assertTrue(rerank.pair_text(chunks()[0]).startswith('Положение › Пропуска\n'))


if __name__ == '__main__':
    unittest.main()
