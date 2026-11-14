"""The generator can run in a local llama-server (GPU); only loopback addresses are used."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import threading
import unittest

from app import rag


class FakeServer(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        FakeServer.seen.append((self.path, body))
        reply = {'status': 'answer', 'short_answer': '', 'items': [{'text': 'Суточные — 700.', 'sources': [1]}], 'missing': []}
        data = json.dumps({'choices': [{'message': {'content': json.dumps(reply, ensure_ascii=False)}}]}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

    def log_message(self, *args):
        pass


class ServerBackendTest(unittest.TestCase):
    def test_remote_address_is_refused(self):
        for url in ('http://10.0.0.5:8080', 'https://api.example.com'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                rag.ServerModel(url)

    def test_request_carries_schema_and_returns_content(self):
        server = HTTPServer(('127.0.0.1', 0), FakeServer)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        model = rag.ServerModel(f'http://127.0.0.1:{server.server_port}')
        content = json.loads(model.complete([{'role': 'user', 'content': 'Сколько суточных?'}]))
        self.assertEqual(content['items'][0]['sources'], [1])
        path, body = FakeServer.seen[-1]
        self.assertEqual(path, '/v1/chat/completions')
        self.assertEqual(body['temperature'], 0)
        self.assertEqual(body['response_format']['json_schema']['schema'], rag.SCHEMA)

    def test_unreachable_server_falls_back(self):
        model = rag.ServerModel('http://127.0.0.1:9')       # nothing listens there
        with self.assertRaises(OSError):
            model.complete([{'role': 'user', 'content': 'x'}])


if __name__ == '__main__':
    unittest.main()
