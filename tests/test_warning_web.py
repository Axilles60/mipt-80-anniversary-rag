from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import quote
from urllib.request import urlopen
import openpyxl
from app.importer import batch

class WarningWebTest(unittest.TestCase):
    def test_formula_warning_at_fragment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); db=root/'db'; manifest=root/'manifest.json'; manifest.write_text('[]')
            book=openpyxl.Workbook(); book.active['F16']='=IFERROR(#REF!,0)'; path=root/'sample.xlsx'; book.save(path)
            self.assertEqual(batch(str(db),[str(path)],str(manifest)), {'review':1})
            with closing(sqlite3.connect(db)) as conn:
                fragment_id=conn.execute('select id from import_fragments where locator like ?',('%/F16',)).fetchone()[0]
            env=dict(os.environ,NAVIGATOR_DB=str(db),PORT='18767')
            server=subprocess.Popen([sys.executable,'-m','app'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                for _ in range(50):
                    try:
                        response=urlopen('http://127.0.0.1:18767/fragment?id='+quote(fragment_id,safe=''),timeout=1).read().decode(); break
                    except OSError: time.sleep(.1)
                else: self.fail('Server failed to start')
                self.assertIn('повреждённую ссылку',response); self.assertIn('F16',response)
            finally:
                server.terminate(); server.wait(timeout=5)
