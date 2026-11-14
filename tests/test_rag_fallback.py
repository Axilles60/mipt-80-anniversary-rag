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


class FailureReasonTest(unittest.TestCase):
    """When no answer is generated, the reason is reported (the page says it plainly)."""
    def reason(self, reply):
        failure = []
        with mock.patch.object(rag, 'llm', return_value=Model(reply)),                 mock.patch.object(rag, 'build_context', return_value=([CHUNK, dict(CHUNK, id=2)], {('S', 'v'): 'Положение'})):
            self.assertIsNone(rag.answer(None, 'Сколько суточных?', failure=failure))
        return failure

    def test_reasons(self):
        self.assertEqual(self.reason(OSError('down')), ['unavailable'])
        self.assertEqual(self.reason('not json'), ['format'])

    def test_no_model(self):
        failure = []
        with mock.patch.object(rag, 'llm', return_value=None):
            self.assertIsNone(rag.answer(None, 'q', failure=failure))
        self.assertEqual(failure, ['unavailable'])

    def test_context_overflow_retries_with_fewer_fragments(self):
        import io
        from urllib.error import HTTPError
        calls = []
        class Overflow:
            def complete(self, messages, schema=None, max_tokens=None):
                calls.append(messages[1]['content'])
                if len(calls) == 1:
                    raise HTTPError('u', 400, 'bad', {}, io.BytesIO(b'request exceeds the available context size'))
                return json.dumps({'status': 'insufficient', 'short_answer': '', 'items': [], 'missing': []})
        with mock.patch.object(rag, 'llm', return_value=Overflow()),                 mock.patch.object(rag, 'build_context', return_value=([CHUNK, dict(CHUNK, id=2)], {('S', 'v'): 'Положение'})):
            result = rag.answer(None, 'Сколько суточных?')
        self.assertIsNotNone(result)
        self.assertEqual(len(calls), 2)
        self.assertLess(len(calls[1]), len(calls[0]))


if __name__ == '__main__':
    unittest.main()
