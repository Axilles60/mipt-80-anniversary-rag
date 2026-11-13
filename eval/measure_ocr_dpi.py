"""Does the OCR render resolution lose small table digits? (synthetic scans only)

Builds fictional scanned pages: an image-only PDF page (1754x1240 px, i.e. an
A4 landscape scan at 150 dpi) with a table of amounts in small print, then runs
the standard PDF extractor (app.importer.pdf) at several NAVIGATOR_OCR_DPI
values and counts amounts recognised exactly.

  python eval/measure_ocr_dpi.py [--dpi 144,300,400] [--report PATH]
Requires Tesseract with the rus language. All numbers are invented.
"""
import argparse
import io
import json
import os
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FONTS = [Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'arial.ttf',
         Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'), Path('/Library/Fonts/Arial.ttf')]


def font(size):
    from PIL import ImageFont
    for path in FONTS:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    raise SystemExit('no TrueType font with Cyrillic found')


def amount(rng):
    whole = rng.randint(1_000, 9_999_999)
    return f'{whole:,}'.replace(',', ' ') + ',' + f'{rng.randint(0, 99):02d}'


def scan(size_px, seed):
    """One image-only PDF page and the amounts printed on it."""
    from PIL import Image, ImageDraw
    import fitz
    rng = random.Random(seed)
    img = Image.new('L', (1754, 1240), 255)
    draw = ImageDraw.Draw(img)
    f = font(size_px)
    draw.text((60, 40), 'Карточка учёта затрат (вымышленная)', font=font(size_px + 4), fill=0)
    columns = [60, 520, 900, 1280]
    for c, title in zip(columns, ['Статья', 'План, руб.', 'Факт, руб.', 'Остаток, руб.']):
        draw.text((c, 110), title, font=f, fill=0)
    amounts = []
    for row in range(12):
        y = 150 + row * (size_px + 14)
        draw.text((columns[0], y), f'{row + 1}. Статья расходов {row + 1}', font=f, fill=0)
        for c in columns[1:]:
            value = amount(rng); amounts.append(value)
            draw.text((c, y), value, font=f, fill=0)
        draw.line((50, y + size_px + 6, 1700, y + size_px + 6), fill=160)
    buf = io.BytesIO(); img.save(buf, format='PNG')
    doc = fitz.open(); page = doc.new_page(width=842, height=595)
    page.insert_image(page.rect, stream=buf.getvalue())
    raw = doc.tobytes(); doc.close()
    return raw, amounts


def recognised(text, amounts):
    compact = text.replace('\u00a0', ' ')
    loose = compact.replace(' ', '')
    return sum(1 for a in amounts if a in compact or a.replace(' ', '') in loose)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dpi', default='144,300,400')
    p.add_argument('--sizes', default='12,14,16', help='font size in scan pixels')
    p.add_argument('--report')
    args = p.parse_args()
    if args.report and Path(args.report).exists():
        raise SystemExit('report exists: ' + args.report)
    from app.importer import pdf
    os.environ.setdefault('NAVIGATOR_OCR_LANG', 'rus+eng')
    result = {'data': 'synthetic', 'scan_px': [1754, 1240], 'runs': []}
    for size in [int(x) for x in args.sizes.split(',')]:
        raw, amounts = scan(size, seed=size)
        for dpi in [int(x) for x in args.dpi.split(',')]:
            os.environ['NAVIGATOR_OCR_DPI'] = str(dpi)
            fragments, _ = pdf(raw)
            text = '\n'.join(t for loc, t, _ in fragments if loc.endswith('/ocr'))
            hit = recognised(text, amounts)
            result['runs'].append({'font_px': size, 'dpi': dpi, 'amounts': len(amounts), 'exact': hit})
            print(f'font {size:2}px  dpi {dpi:3}  exact amounts {hit:2}/{len(amounts)}')
    if args.report:
        Path(args.report).write_text(json.dumps(result, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
