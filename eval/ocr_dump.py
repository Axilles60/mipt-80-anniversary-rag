"""Page-by-page extraction report for human review of OCR (human-in-the-loop).

Runs the same PDF extraction as the standard import (app.importer.pdf: text
layer, and Tesseract OCR for pages that need it) and writes a Markdown file
with, for every page, the flags, the text layer and the OCR text, so that a
person can compare them with the original by eye. ``--force-ocr`` also OCRs
pages that have a text layer, to check the recognition itself.

Run locally; the report contains the document text. Keep it under data/runs/
(ignored by git) when the document is not open data (CASE-07).

  python eval/ocr_dump.py data/originals/archive.zip --list
  python eval/ocr_dump.py data/originals/archive.zip --member "конц зала" --out data/runs/ocr-hall.md
  python eval/ocr_dump.py some.pdf --pages 1-3 --force-ocr --out data/runs/ocr-some.md
"""
import argparse
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def pages_arg(value, count):
    if not value:
        return set(range(1, count + 1))
    out = set()
    for part in value.split(','):
        a, _, b = part.partition('-')
        out.update(range(int(a), int(b or a) + 1))
    return {p for p in out if 1 <= p <= count}


def ocr_page(page, lang):
    from app.importer import ocr_dpi
    with tempfile.TemporaryDirectory() as tmp:
        image = Path(tmp) / 'page.png'
        page.get_pixmap(dpi=ocr_dpi(), alpha=False).save(str(image))
        run = subprocess.run(['tesseract', str(image), 'stdout', '-l', lang], capture_output=True, text=True, encoding='utf-8')
        if run.returncode:
            return None, run.stderr.strip()[:300]
        return run.stdout, None


def load(path, member):
    path = Path(path)
    if path.suffix.lower() != '.zip':
        return path.name, path.read_bytes()
    with zipfile.ZipFile(path) as z:
        names = [i.filename for i in z.infolist() if not i.is_dir()]
        matches = [n for n in names if member and member.lower() in n.lower()]
        if len(matches) != 1:
            raise SystemExit(f'--member must match exactly one file; matched {len(matches)}. Use --list.')
        return Path(matches[0]).name, z.read(matches[0])


def fence(text):
    text = (text or '').rstrip()
    return '```text\n' + (text if text else '(пусто)') + '\n```\n'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('path')
    p.add_argument('--member', help='part of a file name inside the ZIP archive')
    p.add_argument('--list', action='store_true', help='list files inside the ZIP archive and exit')
    p.add_argument('--pages', help='e.g. 1-3,7')
    p.add_argument('--force-ocr', action='store_true', help='OCR every selected page, not only pages without a usable text layer')
    p.add_argument('--lang', default=os.environ.get('NAVIGATOR_OCR_LANG', 'rus+eng'))
    p.add_argument('--out', help='Markdown report path (must not exist); default: print')
    args = p.parse_args()
    if args.list:
        with zipfile.ZipFile(args.path) as z:
            for info in z.infolist():
                if not info.is_dir(): print(info.filename)
        return
    if args.out and Path(args.out).exists():
        raise SystemExit('report exists, choose a new file: ' + args.out)
    import fitz
    from app.importer import pdf
    name, raw = load(args.path, args.member)
    if not name.lower().endswith('.pdf'):
        raise SystemExit('only PDF pages are OCRed; got ' + name)
    fragments, notes = pdf(raw)
    by_page = {}
    for locator, text, meta in fragments:
        by_page.setdefault(meta['page'], {})[meta['type']] = (text, meta)
    doc = fitz.open(stream=raw, filetype='pdf')
    selected = sorted(pages_arg(args.pages, len(doc)))
    out = [f'# OCR для сверки: {name}\n',
           f'- SHA-256: `{hashlib.sha256(raw).hexdigest()}`',
           f'- Страниц в файле: {len(doc)}; в отчёте: {", ".join(map(str, selected))}',
           f'- Языки Tesseract: `{args.lang}`; принудительный OCR: {"да" if args.force_ocr else "нет (как при импорте)"}',
           '- Отметьте для каждой страницы: совпадает / ошибки (какие) / нечитаемо.\n']
    for n in selected:
        entry = by_page.get(n, {})
        text_layer, meta = entry.get('pdf_text', ('', {}))
        ocr_text, ocr_meta = entry.get('ocr', (None, {}))
        ocr_error = None
        if ocr_text is None and args.force_ocr:
            ocr_text, ocr_error = ocr_page(doc[n - 1], args.lang)
            ocr_meta = {'type': 'ocr', 'forced': True}
        flags = [f"OCR при импорте: {'да' if meta.get('ocr_needed') else 'нет'}",
                 f"доля изображений: {meta.get('image_area_ratio', 0):.2f}"]
        if ocr_meta.get('rotation') is not None: flags.append(f"поворот: {ocr_meta['rotation']}°")
        if ocr_meta.get('orientation_uncertain'): flags.append('ориентация не определена')
        if ocr_meta.get('unreadable'): flags.append('OCR не вернул текста')
        if ocr_meta.get('forced'): flags.append('OCR выполнен для сверки (--force-ocr)')
        out.append(f'## Страница {n}\n\n' + '; '.join(flags) + '\n')
        out.append('### Текстовый слой PDF\n\n' + fence(text_layer))
        if ocr_text is not None or ocr_error:
            out.append('### OCR (Tesseract)\n\n' + (fence(ocr_text) if ocr_error is None else f'Ошибка OCR: {ocr_error}\n'))
        out.append('Сверка: ☐ совпадает ☐ есть ошибки ☐ нечитаемо — комментарий: \n')
    if notes:
        out.append('## Замечания импорта\n\n' + '\n'.join('- ' + x for x in notes) + '\n')
    report = '\n'.join(out)
    if args.out:
        target = Path(args.out); target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report, encoding='utf-8')
        print('Report saved locally:', target)
    else:
        print(report)


if __name__ == '__main__':
    main()
