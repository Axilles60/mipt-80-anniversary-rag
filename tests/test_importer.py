from contextlib import closing
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

import fitz
import openpyxl
from PIL import Image, ImageDraw
from app.importer import batch

class ImportTest(unittest.TestCase):
    def test_batch_and_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); db = root/'test.db'; manifest=root/'manifest.json'; manifest.write_text('[]')
            img = Image.new('RGB',(1200,900),'white')
            draw = ImageDraw.Draw(img)
            for n in range(10): draw.text((40,50+n*70), 'SERVICE REQUEST PROCESSING EXAMPLE', fill='black', stroke_width=1)
            b=io.BytesIO(); img.save(b,format='PNG')
            doc=fitz.open(); page=doc.new_page(width=600,height=450); page.insert_image(page.rect,stream=b.getvalue()); page.insert_text((20,20),'Partial layer')
            pdf=root/'scan.pdf'; doc.save(pdf); doc.close()
            book=openpyxl.Workbook(); sheet=book.active; sheet['A1']='Header'; sheet['A2']='=1+2'; sheet.merge_cells('B1:C1'); sheet['B1']='Group'; book.save(root/'book.xlsx')
            (root/'bad.pdf').write_bytes(b'broken')
            old=os.environ.get('NAVIGATOR_OCR_LANG'); os.environ['NAVIGATOR_OCR_LANG']='eng'
            try:
                result=batch(str(db),[str(pdf),str(root/'bad.pdf'),str(root/'book.xlsx')],str(manifest))
                self.assertEqual(result.get('error'),1)
                self.assertEqual(result.get('review'),2)
                con=sqlite3.connect(db); self.addCleanup(con.close)
                self.assertEqual(con.execute("select count(*) from import_fragments where locator='page/1/ocr'").fetchone()[0],1)
                formula=json.loads(con.execute("select metadata from import_fragments where locator like '%/A2'").fetchone()[0])
                self.assertEqual(formula['formula'],'=1+2'); self.assertIsNone(formula['cached_value'])
                self.assertEqual(batch(str(db),[str(pdf)],str(manifest)),{'unchanged':1})
                doc=fitz.open(pdf); doc[0].insert_text((25,40),'New version'); doc.save(root/'new.pdf'); doc.close(); pdf.write_bytes((root/'new.pdf').read_bytes())
                batch(str(db),[str(pdf)],str(manifest))
                self.assertEqual(con.execute("select count(*) from imports where filename='scan.pdf'").fetchone()[0],2)
                self.assertEqual(con.execute("select count(*) from import_fragments where locator='page/1/ocr'").fetchone()[0],2)
                self.assertEqual(con.execute('select count(*) from import_fts').fetchone()[0],con.execute('select count(*) from import_fragments').fetchone()[0])
                con.close()
            finally:
                if old is None: os.environ.pop('NAVIGATOR_OCR_LANG',None)
                else: os.environ['NAVIGATOR_OCR_LANG']=old
    def test_docx_and_audit(self):
        from app.importer import audit
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); doc = root/'sample.docx'; manifest = root/'manifest.json'
            xml = b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Sample paragraph</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'
            with zipfile.ZipFile(doc,'w') as z: z.writestr('word/document.xml', xml)
            import hashlib
            sha = hashlib.sha256(doc.read_bytes()).hexdigest()
            manifest.write_text(json.dumps([{'id':f'SRC-{i:02}','name':'sample.docx' if i==1 else f'{i}.pdf','sha256':sha if i==1 else 'none'} for i in range(1,21)]))
            self.assertEqual(batch(str(root/'db'),[str(doc)],str(manifest)), {'review':1})
            result=audit(str(root/'db'),str(manifest))
            self.assertEqual(len(result),20); self.assertTrue(result[0]['matching_manifest_checksum'])
            with closing(sqlite3.connect(root/'db')) as db:
                self.assertEqual(db.execute('select count(*) from import_fragments').fetchone()[0],2)

    def test_zip_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); z=root/'test.zip'; manifest=root/'manifest.json'; manifest.write_text('[]')
            with zipfile.ZipFile(z,'w') as f:
                f.writestr('../escape.pdf',b'x'); f.writestr('__MACOSX/._x',b'x'); f.writestr('bad.pdf',b'bad')
            self.assertEqual(batch(str(root/'db'),[str(z)],str(manifest)),{'error':2})
            self.assertFalse((root.parent/'escape.pdf').exists())

if __name__ == '__main__': unittest.main()
