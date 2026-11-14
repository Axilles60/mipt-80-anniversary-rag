import io
import json
import sqlite3
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
import fitz
import openpyxl
from PIL import Image
from app.importer import pdf, xlsx
from app.__main__ import render_import_fragment
from app.evidence import load, query

class AcceptanceGapTests(unittest.TestCase):
    def test_unreadable_scan_flagged(self):
        image=io.BytesIO(); Image.new('RGB',(300,300),'white').save(image,format='PNG')
        doc=fitz.open(); page=doc.new_page(); page.insert_image(page.rect,stream=image.getvalue()); raw=doc.tobytes(); doc.close()
        osd=subprocess.CompletedProcess([],1,stdout='',stderr='no orientation')
        empty=subprocess.CompletedProcess([],0,stdout='  ',stderr='')
        with patch('app.importer.subprocess.run',side_effect=[osd,empty]): fragments,notes=pdf(raw)
        ocr=next(meta for _,_,meta in fragments if meta['type']=='ocr')
        self.assertTrue(ocr['unreadable']); self.assertTrue(ocr['orientation_uncertain'])
        self.assertTrue(any('no readable text' in x for x in notes))
        self.assertIn('не распознал',render_import_fragment({'metadata':json.dumps(ocr),'locator':'page/1/ocr','text':''},True))
    def test_broken_formula_is_recorded_not_calculated(self):
        book=openpyxl.Workbook(); book.active['F16']='=IFERROR(#REF!,0)'; raw=io.BytesIO(); book.save(raw)
        fragments,notes=xlsx(raw.getvalue()); meta=next(m for _,_,m in fragments if m['range']=='F16')
        self.assertTrue(meta['formula_error']); self.assertIn('#REF!',meta['formula']); self.assertIsNone(meta['cached_value']); self.assertTrue(notes)
        self.assertIn('повреждённую ссылку',render_import_fragment({'metadata':json.dumps(meta),'locator':'F16','text':'formula'},True))
    def test_missing_instruction_and_conflict(self):
        db=sqlite3.connect(':memory:'); self.addCleanup(db.close); db.row_factory=sqlite3.Row
        catalog=json.loads(Path('fixtures/evidence/synthetic.json').read_text())
        catalog['limits'].append({'id':'missing-instruction','document_id':'MISSING','topic':'application','description':'Инструкция отсутствует.','basis':'M1'})
        load(db,catalog)
        result=query(db,'Порядок заявки','2023-01-01',{'категория':'Б'})
        self.assertTrue(any(x['basis']=='M1' and 'отсутствует' in x['text'] for x in result['limits']))
        self.assertEqual({x['basis'] for x in result['items']} & {'C1','C2'},{'C1','C2'})
        self.assertTrue(result['conflicts']); db.close()

if __name__=='__main__': unittest.main()
