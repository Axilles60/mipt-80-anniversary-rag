import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]

class Flow(unittest.TestCase):
    def test_web_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, NAVIGATOR_DB=str(Path(tmp)/'db.sqlite'), PORT='18765')
            for _ in range(2):
                subprocess.run([sys.executable, '-m', 'app', 'import-fixture'], cwd=ROOT, env=env, check=True, capture_output=True)
            server = subprocess.Popen([sys.executable, '-m', 'app'], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(50):
                    try:
                        page = urlopen('http://127.0.0.1:18765/', timeout=1).read().decode()
                        break
                    except OSError:
                        time.sleep(.1)
                else:
                    self.fail('server did not start')
                self.assertIn('<form', page)
                def ask(q):
                    return urlopen('http://127.0.0.1:18765/ask', data=urlencode({'question': q}).encode()).read().decode()
                answer = ask('Не работает принтер, куда обратиться и что написать?')
                self.assertIn('16 рабочих часов', answer)
                self.assertIn('не является сроком устранения', answer)
                for n in range(1, 4):
                    self.assertIn(f'SYN-SUPPORT-v1-p{n}', answer)
                source = urlopen('http://127.0.0.1:18765/fragment?id=SYN-SUPPORT-v1-p3').read().decode()
                self.assertIn('п. 3', source)
                self.assertIn('п. 1', source)
                self.assertIn('16 рабочих часов', source)
                self.assertIn('Недостаточно данных', ask('Как оформить отпуск?'))
                with self.assertRaises(HTTPError) as empty:
                    ask('   ')
                self.assertEqual(empty.exception.code, 400)
                self.assertIn('Введите вопрос', empty.exception.read().decode())
                Path(env['NAVIGATOR_DB']).unlink()
                with self.assertRaises(HTTPError) as broken:
                    ask('Не работает принтер')
                self.assertEqual(broken.exception.code, 503)
                self.assertIn('Технический сбой', broken.exception.read().decode())
            finally:
                server.terminate()
                server.wait(timeout=5)

if __name__ == '__main__':
    unittest.main()
