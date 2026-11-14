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


class NoRedirectTest(unittest.TestCase):
    """The generator request carries document text: a redirect from the local server is refused."""
    def test_redirect_is_not_followed(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from urllib.error import HTTPError
        hits = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                hits.append(self.path)
                self.send_response(307); self.send_header('Location', '/elsewhere'); self.send_header('Content-Length', '0')
                self.end_headers()
            def log_message(self, *a): pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            model = rag.ServerModel(f'http://127.0.0.1:{server.server_port}')
            with self.assertRaises(HTTPError):
                model.complete([{'role': 'user', 'content': 'x'}])
            self.assertEqual(hits, ['/v1/chat/completions'])
        finally:
            server.shutdown(); server.server_close()


class TruncatedReplyTest(unittest.TestCase):
    """A long list answer cut off at max_tokens: not «неверный формат» — its whole statements are kept."""
    WHOLE = json.dumps({'status': 'answer', 'short_answer': 'Суточные — 700 условных единиц.', 'missing': [],
                        'items': [{'text': 'Суточные выплачиваются в размере 700 условных единиц.', 'sources': [1]},
                                  {'text': 'Суточные выплачиваются за каждый день командировки.', 'sources': [1]}]},
                       ensure_ascii=False)

    def cut(self, at):
        return self.WHOLE[:self.WHOLE.index(at)]

    def test_salvage_keeps_whole_items_only(self):
        content = rag.salvage(self.cut('за каждый день'))
        self.assertEqual([i['text'] for i in content['items']], ['Суточные выплачиваются в размере 700 условных единиц.'])
        self.assertEqual(content['status'], 'answer')
        self.assertIsNone(rag.salvage(self.cut('Суточные выплачиваются в размере')))   # not one whole statement
        self.assertIsNone(rag.salvage('{"status": "ans'))

    def test_server_reply_cut_off_raises_truncated(self):
        out = {'choices': [{'finish_reason': 'length', 'message': {'content': '{"status": "answer"'}}]}
        with self.assertRaises(rag.Truncated):
            rag._content(out, None)
        self.assertEqual(rag._content(out, False), '{"status": "answer"')   # plain text: kept as is

    def answer(self, text):
        failure = []
        with mock.patch.object(rag, 'llm', return_value=Model(rag.Truncated(text))), \
                mock.patch.object(rag, 'build_context', return_value=([CHUNK], {('S', 'v'): 'Положение'})):
            return rag.answer(None, 'Сколько суточных?', failure=failure), failure

    def test_answer_from_a_cut_off_reply(self):
        result, failure = self.answer(self.cut('за каждый день'))
        self.assertTrue(result['truncated'])
        self.assertEqual([i['text'] for i in result['items']], ['Суточные выплачиваются в размере 700 условных единиц.'])
        from app.__main__ import render_rag
        with mock.patch('app.__main__.cite', return_value=''), mock.patch('app.requisites.status', return_value={}):
            self.assertIn('показана его проверенная часть', render_rag(None, result, None, 'Сколько суточных?'))

    def test_nothing_whole_is_its_own_reason(self):
        result, failure = self.answer(self.cut('Суточные выплачиваются в размере'))
        self.assertIsNone(result)
        self.assertEqual(failure, ['length'])
        self.assertIn('слишком длинным', rag.FAILURES['length'])


if __name__ == '__main__':
    unittest.main()
