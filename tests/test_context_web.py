"""Synthetic formats exercise context through the real import and HTTP endpoints."""
from contextlib import closing
import io
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin
from urllib.request import urlopen
import zipfile

import fitz
import openpyxl
from PIL import Image, ImageDraw, ImageFont
from app.core import connect

ROOT = Path(__file__).resolve().parents[1]

class LinkParser(HTMLParser):
    def __init__(self): super().__init__(); self.links=[]
    def handle_starttag(self, tag, attrs):
        if tag=='a' and dict(attrs).get('href','').startswith('/fragment?'): self.links.append(dict(attrs)['href'])

class ContextWebTest(unittest.TestCase):
    def test_context_through_import_and_web(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); db=root/'db.sqlite'; manifest=root/'manifest.json'; manifest.write_text('[]')
            # Partial PDF text layer over a large raster page; English OCR data is local.
            img=Image.new('RGB',(1600,900),'white'); draw=ImageDraw.Draw(img)
            font=ImageFont.load_default(size=44)
            for i in range(6): draw.text((45,55+i*120),'ZEPHYROCR IMAGINARY POLICY EXAMPLE',font=font,fill='black')
            buf=io.BytesIO(); img.save(buf,format='PNG'); pdf=fitz.open(); page=pdf.new_page(width=800,height=450)
            page.insert_image(page.rect,stream=buf.getvalue()); page.insert_text((20,20),'ONLY PARTIAL TEXT LAYER')
            pdf_path=root/'sample.pdf'; pdf.save(pdf_path); pdf.close()
            # Small valid OOXML package with one paragraph reference and table.
            w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
            xml=f'<w:document xmlns:w="{w}"><w:body><w:p><w:r><w:t>FLUXXFOOT paragraph text.</w:t></w:r><w:r><w:footnoteReference w:id="7"/></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Fictional cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'
            foot=f'<w:footnotes xmlns:w="{w}"><w:footnote w:id="7"><w:p><w:r><w:t>FOOTNOTE-ONLY fictional limitation.</w:t></w:r></w:p></w:footnote></w:footnotes>'
            docx=root/'notes.docx'
            with zipfile.ZipFile(docx,'w') as z: z.writestr('word/document.xml',xml); z.writestr('word/footnotes.xml',foot)
            xlsx=root/'table.xlsx'; wb=openpyxl.Workbook(); sh=wb.active; sh.title='Example'
            sh['A1']='Rows'; sh.merge_cells('B1:C1'); sh['B1']='North branch'; sh.merge_cells('D1:E1'); sh['D1']='South branch'
            sh['B2']='Blue category'; sh['C2']='Red category'; sh['D2']='Green category'; sh['E2']='Yellow category'
            sh['A3']='Imaginary row'; sh['B3']='COBALTVAL'; sh['C3']='NEIGHBORVAL'; sh['D3']='OTHERVAL'; sh['E3']='=IFERROR(#REF!,0)'; wb.save(xlsx); wb.close()
            # Write a synthetic saved formula value without recalculating the formula.
            with zipfile.ZipFile(xlsx) as source:
                members={name:source.read(name) for name in source.namelist()}
            xml_sheet=members['xl/worksheets/sheet1.xml'].decode()
            xml_sheet=xml_sheet.replace('<f>IFERROR(#REF!,0)</f><v />', '<f>IFERROR(#REF!,0)</f><v>0</v>')
            members['xl/worksheets/sheet1.xml']=xml_sheet.encode()
            with zipfile.ZipFile(xlsx,'w') as target:
                for name,content in members.items(): target.writestr(name,content)
            env=dict(os.environ,NAVIGATOR_DB=str(db),NAVIGATOR_OCR_LANG='eng')
            subprocess.run([sys.executable,'-m','app','import',str(pdf_path),str(docx),str(xlsx),'--manifest',str(manifest),'--report',str(root/'report.json')],cwd=ROOT,env=env,check=True,capture_output=True)
            self.assertEqual(json.loads((root/'report.json').read_text()),{'review':3})
            with closing(connect(str(db))) as con:
                rows=[dict(x) for x in con.execute('select f.*,d.filename from import_fragments f join imports d on d.source_id=f.source_id and d.version=f.version')]
            def select(name,locator): return next(r for r in rows if r['filename']==name and r['locator']==locator)
            para=select('notes.docx','body/1/paragraph'); cell=select('table.xlsx','sheet/Example/cell/B3'); formula=select('table.xlsx','sheet/Example/cell/E3'); ocr=select('sample.pdf','page/1/ocr')
            self.assertEqual(json.loads(para['metadata'])['footnote_ids'],['7'])
            meta=json.loads(cell['metadata']); self.assertIn('North branch',str(meta['column_headers'])); self.assertIn('Blue category',str(meta['column_headers'])); self.assertIn('Imaginary row',str(meta['row_headers'])); self.assertNotIn('Red category',str(meta['column_headers']))
            fm=json.loads(formula['metadata']); self.assertTrue(fm['formula_error']); self.assertEqual(fm['cached_value'],0); self.assertIn('#REF!',fm['formula'])
            self.assertTrue(json.loads(ocr['metadata'])['requires_visual_review'])
            with socket.socket() as sock: sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
            env['PORT']=str(port); server=subprocess.Popen([sys.executable,'-m','app'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); base=f'http://127.0.0.1:{port}'
            def ask(q): return urlopen(base+'/ask',data=urlencode({'question':q}).encode(),timeout=3).read().decode()
            def source(page):
                parser=LinkParser(); parser.feed(page); self.assertTrue(parser.links); return urlopen(urljoin(base,parser.links[0]),timeout=3).read().decode()
            try:
                for _ in range(50):
                    try: urlopen(base+'/',timeout=1).close(); break
                    except OSError: time.sleep(.1)
                else: self.fail('server unavailable')
                para_response=ask('FLUXXFOOT'); para_source=source(para_response); self.assertIn('FOOTNOTE-ONLY',para_source)
                parser=LinkParser(); parser.feed(para_response); previous_href=parser.links[0]
                with zipfile.ZipFile(docx,'w') as z:
                    z.writestr('word/document.xml',xml)
                    z.writestr('word/footnotes.xml',foot.replace('FOOTNOTE-ONLY','FOOTNOTE-NEW'))
                subprocess.run([sys.executable,'-m','app','import',str(docx),'--manifest',str(manifest),'--report',str(root/'revision.json')],cwd=ROOT,env=env,check=True,capture_output=True)
                old_source=urlopen(urljoin(base,previous_href),timeout=3).read().decode()
                self.assertIn('FOOTNOTE-ONLY',old_source); self.assertNotIn('FOOTNOTE-NEW',old_source)
                answer=ask('Example COBALTVAL'); self.assertIn('COBALTVAL',answer); self.assertNotIn('NEIGHBORVAL',answer)
                requested_pair=ask('COBALTVAL NEIGHBORVAL'); self.assertIn('COBALTVAL',requested_pair); self.assertIn('NEIGHBORVAL',requested_pair)
                cell_source=source(answer); self.assertIn('North branch',cell_source); self.assertIn('Blue category',cell_source); self.assertIn('Imaginary row',cell_source); self.assertNotIn('NEIGHBORVAL',cell_source)
                formula_answer=ask('IFERROR'); self.assertNotIn('Подтверждённые сведения',formula_answer); self.assertIn('поврежд',formula_answer.lower()); formula_source=source(formula_answer); self.assertIn('сохранённое значение: 0',formula_source)
                recognized = re.search(r'[A-Za-z]{6,}',ocr['text'])
                self.assertIsNotNone(recognized, 'Synthetic OCR produced no searchable token')
                ocr_answer=ask(recognized.group()); self.assertNotIn('Подтверждённые сведения',ocr_answer); self.assertIn('OCR',ocr_answer); self.assertIn('сверк',ocr_answer.lower())
                layer=select('sample.pdf','page/1/text-layer')
                docs=[]; fragments=[]; rules=[]; topics=[]
                for n,row in enumerate((ocr,formula,layer)):
                    key=f'unsafe{n}'; docs.append({'id':key,'title':key,'signed':'2020-01-01','effective':'2020-01-01','valid_from':'2020-01-01','valid_to':None,'approval':'confirmed'})
                    fragments.append({'id':row['id'],'document_id':key,'locator':row['locator'],'text':row['text']})
                    rules.append({'id':key,'document_id':key,'fragment_id':row['id'],'topic':key,'kind':'norm','start':'2020-01-01','end':None,'conditions':{},'exceptions':{}})
                    topics.append({'id':key,'keywords':[key]})
                catalog=root/'catalog.json'; catalog.write_text(json.dumps({'documents':docs,'fragments':fragments,'rules':rules,'topics':topics,'relations':[],'limits':[]}))
                subprocess.run([sys.executable,'-m','app','load-evidence',str(catalog)],cwd=ROOT,env=env,check=True,capture_output=True)
                for n in range(3):
                    response=urlopen(base+'/ask',data=urlencode({'question':f'unsafe{n}','date':'2023-01-01'}).encode(),timeout=3).read().decode()
                    self.assertNotIn('Подтверждённые сведения',response)
                    self.assertIn('Недостаточно подтверждённых',response)
                    self.assertIn('Ограничение:',response)
            finally:
                server.terminate(); server.wait(timeout=5)

if __name__=='__main__': unittest.main()
