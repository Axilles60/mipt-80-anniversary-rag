import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import urlencode
from urllib.request import urlopen

class EvidenceWebTests(unittest.TestCase):
    def test_evidence_through_web(self):
        with tempfile.TemporaryDirectory() as tmp:
            env=dict(os.environ, NAVIGATOR_DB=str(Path(tmp)/'db.sqlite'), PORT='18766')
            subprocess.run([sys.executable,'-m','app','load-evidence','fixtures/evidence/synthetic.json'],env=env,check=True,capture_output=True)
            server=subprocess.Popen([sys.executable,'-m','app'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                for _ in range(50):
                    try:
                        urlopen('http://127.0.0.1:18766/').close(); break
                    except OSError: time.sleep(.1)
                data=urlencode({'question':'Порядок заявки','date':'2023-01-01','category':'Б'}).encode()
                page=urlopen('http://127.0.0.1:18766/ask',data=data).read().decode()
                self.assertIn('противоречие',page); self.assertIn('C1',page); self.assertIn('C2',page)
                source=urlopen('http://127.0.0.1:18766/fragment?id=C1').read().decode()
                self.assertIn('п. 1',source); self.assertIn('п. 2',source)
            finally:
                server.terminate(); server.wait(timeout=5)
