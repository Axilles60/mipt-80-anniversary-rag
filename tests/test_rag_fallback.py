"""An answer whose statements all fail verification is «no data», not the extractive fallback.

The extractive answer does not pass the same checks; it is used only when the
generator itself is unavailable or returns broken output.
"""
import json
import unittest
from unittest import mock

from app import rag

CHUNK = {'id': 1, 'source_id': 'S', 'version': 'v', 'fragment_id': 'S@v#p/1', 'start': 0, 'end': 10, 'ord': 1,
         'text': '2.1. Суточные выплачиваются в размере 700 условных единиц за каждый день командировки.', 'meta': {}}


class Model:
    def __init__(self, reply): self.reply = reply
    def complete(self, messages, schema=None, max_tokens=None):
        if isinstance(self.reply, Exception): raise self.reply
        return self.reply


def run(reply):
    with mock.patch.object(rag, 'llm', return_value=Model(reply)), \
            mock.patch.object(rag, 'build_context', return_value=([CHUNK], {('S', 'v'): 'Положение'})):
        return rag.answer(None, 'Сколько суточных за границей?')


class FallbackTest(unittest.TestCase):
    def test_unverified_answer_is_no_data(self):
        reply = json.dumps({'status': 'answer', 'short_answer': '', 'missing': [],
                            'items': [{'text': 'Суточные за границей — 2500 условных единиц.', 'sources': [1]}]})
        result = run(reply)
        self.assertIsNotNone(result)
        self.assertEqual(result['status'], 'insufficient')
        self.assertEqual(result['items'], [])

    def test_broken_output_falls_back(self):
        self.assertIsNone(run('not json'))

    def test_unavailable_server_falls_back(self):
        self.assertIsNone(run(OSError('down')))


if __name__ == '__main__':
    unittest.main()
