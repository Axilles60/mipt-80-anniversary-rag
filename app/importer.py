"""Local, versioned batch import. Extracted document text is untrusted data."""
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import zipfile
from xml.etree import ElementTree as ET

from .core import connect

SCHEMA = '''
CREATE TABLE IF NOT EXISTS documents (id TEXT, version TEXT, title TEXT, sha256 TEXT, original BLOB, PRIMARY KEY(id,version));
CREATE TABLE IF NOT EXISTS fragments (id TEXT PRIMARY KEY, document_id TEXT, version TEXT, locator TEXT, text TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS fragments_fts USING fts5(id UNINDEXED, text, tokenize='unicode61');
CREATE TABLE IF NOT EXISTS imports (source_id TEXT, version TEXT, filename TEXT, sha256 TEXT, format TEXT, status TEXT, detail TEXT, original BLOB, PRIMARY KEY(source_id,version));
CREATE TABLE IF NOT EXISTS import_fragments (id TEXT PRIMARY KEY, source_id TEXT, version TEXT, locator TEXT, text TEXT, metadata TEXT);
CREATE TABLE IF NOT EXISTS import_events (source_id TEXT, filename TEXT, sha256 TEXT, status TEXT, detail TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE VIRTUAL TABLE IF NOT EXISTS import_fts USING fts5(id UNINDEXED, text);
'''
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def xml_text(element):
    return ''.join(x.text or '' for x in element.iter(W + 't'))

def docx(raw):
    out = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        root = ET.fromstring(z.read('word/document.xml'))
        body = root.find(W + 'body')
        for index, child in enumerate(body, 1):
            if child.tag == W + 'p':
                out.append((f'body/{index}/paragraph', xml_text(child), {'type':'paragraph', 'footnote_ids':[ref.get(W+'id') for ref in child.iter(W+'footnoteReference') if ref.get(W+'id')], 'style': (child.find(W+'pPr/'+W+'pStyle').get(W+'val') if child.find(W+'pPr/'+W+'pStyle') is not None else None)}))
            elif child.tag == W + 'tbl':
                for r, row in enumerate(child.findall(W+'tr'), 1):
                    cells = [xml_text(cell) for cell in row.findall(W+'tc')]
                    out.append((f'body/{index}/table/row/{r}', ' | '.join(cells), {'type':'table_row','cells':cells,'footnote_ids':[ref.get(W+'id') for ref in row.iter(W+'footnoteReference') if ref.get(W+'id')]}))
        if 'word/footnotes.xml' in z.namelist():
            root = ET.fromstring(z.read('word/footnotes.xml'))
            for foot in root.findall(W+'footnote'):
                fid = foot.get(W+'id')
                if int(fid) >= 0:
                    out.append((f'footnote/{fid}', xml_text(foot), {'type':'footnote'}))
    return out, ['DOCX: embedded objects, headers and complex table merges require manual review']

def xlsx(raw):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=False)
    cached = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)
    out = []
    for sheet in wb:
        # A merged column group followed by a subheader gives a conservative
        # candidate header boundary; other layouts remain subject to review.
        groups = [r for r in sheet.merged_cells.ranges if r.min_col > 1 and r.min_row <= 10]
        label_end = min((r.min_col for r in groups), default=2) - 1
        header_end = max((r.max_row for r in groups), default=0) + 1 if groups else 1
        def resolved(row, col):
            for merged in sheet.merged_cells.ranges:
                if merged.min_row <= row <= merged.max_row and merged.min_col <= col <= merged.max_col:
                    return sheet.cell(merged.min_row, merged.min_col)
            return sheet.cell(row, col)
        def headers(coordinates):
            values, seen = [], set()
            for row, col in coordinates:
                source = resolved(row, col)
                if source.coordinate not in seen and source.value is not None:
                    values.append({'coordinate':source.coordinate, 'text':str(source.value)})
                    seen.add(source.coordinate)
            return values
        for row in sheet.iter_rows():
            for cell in row:
                if cell.value is None:
                    continue
                value = cell.value
                formula = value if cell.data_type == 'f' else None
                saved = cached[sheet.title][cell.coordinate].value
                merge = next((str(r) for r in sheet.merged_cells.ranges if cell.coordinate in r), None)
                header = [str(c.value) if c.value is not None else '' for c in sheet[1]]
                formula_error = bool(formula and '#REF!' in formula.upper())
                column_headers = headers((r, cell.column) for r in range(1, min(cell.row, header_end + 1)))
                row_headers = headers((cell.row, c) for c in range(1, min(cell.column, label_end + 1)))
                metadata = {'column_headers':column_headers, 'row_headers':row_headers, 'header_inferred':True, 'formula_error': formula_error, 'type':'cell', 'sheet':sheet.title, 'range':cell.coordinate, 'header_row_1':header, 'merged_range':merge, 'formula':formula, 'cached_value':saved, 'value':None if formula else value, 'row_hidden':bool(sheet.row_dimensions[cell.row].hidden), 'column_hidden':bool(sheet.column_dimensions[cell.column_letter].hidden)}
                out.append((f'sheet/{sheet.title}/cell/{cell.coordinate}', f'{sheet.title} {cell.coordinate} {value}' + (f' {saved}' if formula and saved is not None else ''), metadata))
    return out, ['XLSX: inferred headers and cached formula values require review']

def ocr_dpi():
    try:
        return max(72, min(600, int(os.environ.get('NAVIGATOR_OCR_DPI', '400'))))
    except ValueError:
        return 400

def pdf(raw):
    import fitz
    from PIL import Image
    out, notes = [], []
    doc = fitz.open(stream=raw, filetype='pdf')
    if doc.needs_pass:
        raise ValueError('encrypted PDF')
    for n, page in enumerate(doc, 1):
        text = page.get_text('text')
        images = page.get_image_info()
        # Every page with a substantial raster image is OCRed, even if it has text.
        area = page.rect.width * page.rect.height
        coverage = min(1.0, sum(max(0, (i['bbox'][2]-i['bbox'][0])*(i['bbox'][3]-i['bbox'][1])) for i in images) / area) if area else 0
        needs_ocr = len(text.strip()) < 40 or coverage > .15
        out.append((f'page/{n}/text-layer', text, {'type':'pdf_text','page':n,'image_area_ratio':coverage,'ocr_needed':needs_ocr}))
        if needs_ocr:
            with tempfile.TemporaryDirectory() as tmp:
                imagepath = Path(tmp)/'page.png'
                # 144 dpi (the former Matrix(2,2)) downsamples typical scans and
                # loses small table digits: 0/12 amounts on a scanned cost card
                # versus 12/12 at 400 dpi.
                page.get_pixmap(dpi=ocr_dpi(), alpha=False).save(str(imagepath))
                osd = subprocess.run(['tesseract', str(imagepath), 'stdout', '--psm', '0'], capture_output=True, text=True)
                match = re.search(r'Rotate:\s*(\d+)', osd.stdout)
                rotation = int(match.group(1)) if match else None
                if rotation:
                    im = Image.open(imagepath)
                    im.rotate(-rotation, expand=True).save(imagepath)
                lang = os.environ.get('NAVIGATOR_OCR_LANG', 'rus+eng')
                run = subprocess.run(['tesseract', str(imagepath), 'stdout', '-l', lang], capture_output=True, text=True)
                if run.returncode:
                    raise RuntimeError('OCR failed: ' + run.stderr.strip()[:500])
                out.append((f'page/{n}/ocr', run.stdout, {'type':'ocr','page':n,'rotation':rotation,'orientation_uncertain':rotation is None,'language':lang,'requires_visual_review':True,'unreadable':not bool(run.stdout.strip())}))
                notes.append(f'page {n}: OCR requires visual verification' + ('; OCR returned no readable text' if not run.stdout.strip() else '') + ('; orientation undetermined' if rotation is None else ''))
    if not out:
        raise ValueError('PDF has no pages')
    return out, notes

def init(db):
    db.executescript(SCHEMA)

def record_error(db, sid, name, sha, exc):
    db.execute('INSERT INTO import_events(source_id,filename,sha256,status,detail) VALUES (?,?,?,?,?)', (sid, name, sha, 'error', str(exc)[:1000]))
    db.commit()

def import_one(db, sid, name, raw):
    sha = digest(raw)
    ext = Path(name).suffix.lower()
    try:
        existing = db.execute('SELECT version,status FROM imports WHERE source_id=? AND sha256=?', (sid,sha)).fetchone()
        if existing:
            # The original, its fragments and the FTS rows are committed as a
            # unit. Older or externally interrupted databases can still have
            # missing, duplicated or stale index entries: reconcile only this
            # version without replacing its fragments or changing their IDs.
            with db:
                rows = db.execute('SELECT id,text FROM import_fragments WHERE source_id=? AND version=?',
                                  (sid, existing['version'])).fetchall()
                if not rows:
                    raise ValueError('Existing version has no fragments; restore the database from a verified backup')
                repaired = 0
                for row in rows:
                    indexed = db.execute('SELECT text FROM import_fts WHERE id=?', (row['id'],)).fetchall()
                    if len(indexed) != 1 or indexed[0]['text'] != row['text']:
                        db.execute('DELETE FROM import_fts WHERE id=?', (row['id'],))
                        db.execute('INSERT INTO import_fts VALUES (?,?)', (row['id'], row['text']))
                        repaired += 1
                status = 'reindexed' if repaired else 'unchanged'
                detail = json.dumps({'version':existing['version'], 'repaired_fragments':repaired})
                db.execute('INSERT INTO import_events(source_id,filename,sha256,status,detail) VALUES (?,?,?,?,?)',
                           (sid,name,sha,status,detail))
            return status
        extractor = {'.pdf':pdf, '.docx':docx, '.xlsx':xlsx}.get(ext)
        if not extractor:
            raise ValueError('unsupported format: ' + ext)
        fragments, notes = extractor(raw)
        if not fragments:
            raise ValueError('no extractable fragments')
        version = sha
        status = 'review' if notes else 'ok'
        with db:
            db.execute('INSERT INTO imports VALUES (?,?,?,?,?,?,?,?)', (sid,version,name,sha,ext,status,json.dumps(notes,ensure_ascii=False),raw))
            for locator, text, metadata in fragments:
                fid = sid + '@' + version + '#' + locator
                db.execute('INSERT INTO import_fragments VALUES (?,?,?,?,?,?)', (fid,sid,version,locator,text,json.dumps(metadata,ensure_ascii=False,default=str)))
                db.execute('INSERT INTO import_fts VALUES (?,?)', (fid,text))
            db.execute('INSERT INTO import_events(source_id,filename,sha256,status,detail) VALUES (?,?,?,?,?)', (sid,name,sha,status,json.dumps(notes,ensure_ascii=False)))
        return status
    except Exception as exc:
        db.rollback()
        record_error(db,sid,name,sha,exc)
        return 'error'

def safe_zip_name(name):
    p = PurePosixPath(name)
    return bool(name) and not p.is_absolute() and not re.match(r'^[A-Za-z]:', name) and '\\' not in name and '..' not in p.parts and '__MACOSX' not in p.parts and not any(part.startswith('._') for part in p.parts)

def batch(db_path, paths, manifest_path=None):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(Path(manifest_path).read_text()) if manifest_path else []
    hashes = {item['sha256']:item['id'] for item in manifest if item['id'] != 'SRC-00'}
    names = {item['name']:item['id'] for item in manifest if item['id'] != 'SRC-00'}
    counts = {}
    with closing(connect(db_path)) as db, db:
        init(db)
        def process(name, raw):
            sid = hashes.get(digest(raw)) or names.get(Path(name).name) or 'FILE-' + hashlib.sha256(name.encode()).hexdigest()[:16]
            status = import_one(db,sid,name,raw)
            counts[status] = counts.get(status,0)+1
        for path in paths:
            try:
                p = Path(path)
                if p.suffix.lower() == '.zip':
                    with zipfile.ZipFile(p) as z:
                        for info in z.infolist():
                            if info.is_dir() or '__MACOSX' in PurePosixPath(info.filename).parts or PurePosixPath(info.filename).name.startswith('._'):
                                continue
                            if not safe_zip_name(info.filename) or info.file_size > 200_000_000:
                                record_error(db,'ZIP-ERROR',info.filename,None,ValueError('unsafe path or oversized entry'))
                                counts['error'] = counts.get('error',0)+1
                                continue
                            try:
                                process(info.filename,z.read(info))
                            except Exception as exc:
                                record_error(db,'ZIP-ERROR',info.filename,None,exc)
                                counts['error'] = counts.get('error',0)+1
                else:
                    process(p.name,p.read_bytes())
            except Exception as exc:
                record_error(db,'FILE-ERROR',str(path),None,exc)
                counts['error'] = counts.get('error',0)+1
    return counts

def audit(db_path, manifest_path):
    manifest = json.loads(Path(manifest_path).read_text())
    with closing(connect(db_path)) as db, db:
        init(db)
        result = []
        for item in manifest:
            if item['id'] == 'SRC-00':
                continue
            rows = db.execute('SELECT version,status,sha256 FROM imports WHERE source_id=?', (item['id'],)).fetchall()
            errors = db.execute("SELECT count(*) FROM import_events WHERE source_id=? AND status='error'", (item['id'],)).fetchone()[0]
            result.append({'id':item['id'],'versions':len(rows),'matching_manifest_checksum':any(r['sha256']==item['sha256'] for r in rows),'statuses':[r['status'] for r in rows], 'errors':errors})
        return result
