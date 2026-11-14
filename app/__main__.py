"""Local-only HTTP interface and fixture import."""
from contextlib import closing
import argparse
import html
import os
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse
from .core import DEFAULT_DB, connect, connect_existing, form_answer, import_fixture, imported_parts
from . import history

# Set while the documents of NAVIGATOR_IMPORT_DIR are being imported at start.
IMPORTING = {'active': False, 'last': ''}

# One inline stylesheet for every page: the service runs offline, so only system fonts.
# Reading column ~70 characters; light and dark themes follow the system setting.
CSS = '''
:root{color-scheme:light dark;
 --bg:#f6f5f1;--surface:#fff;--fg:#1c1f24;--muted:#5a616c;--line:#e0ddd4;--line-strong:#c2beb2;
 --accent:#1b5a8a;--accent-hover:#134468;--accent-ink:#fff;--accent-soft:#e1eaf2;
 --q:#eae8e1;--limit:#f6efdc;--limit-line:#e2d4a8;--gap:#ecedef;--mark:#fae38f;
 --shadow:rgba(28,31,36,.14)}
@media (prefers-color-scheme:dark){:root{
 --bg:#141619;--surface:#1b1e22;--fg:#e6e4df;--muted:#a2a8b1;--line:#2b2f35;--line-strong:#434852;
 --accent:#8db9e4;--accent-hover:#b1d0ee;--accent-ink:#0e1822;--accent-soft:#1e2b38;
 --q:#23272c;--limit:#29251a;--limit-line:#4b432a;--gap:#22262b;--mark:#6a5413;
 --shadow:rgba(0,0,0,.5)}}
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;scrollbar-color:var(--line-strong) var(--bg)}
body{margin:0;min-height:100vh;min-height:100dvh;display:flex;flex-direction:column;background:var(--bg);color:var(--fg);
 font:1rem/1.6 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
 caret-color:var(--accent);accent-color:var(--accent)}
::selection{background:var(--accent-soft);color:var(--fg)}
a{color:var(--accent);text-decoration-thickness:1px;text-underline-offset:.2em}
a:hover{color:var(--accent-hover)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.top{border-bottom:1px solid var(--line)}
.bar,.wrap{width:100%;max-width:46rem;margin:0 auto;padding-inline:16px}
.bar{display:flex;align-items:center;justify-content:space-between;gap:16px;min-height:56px}
h1{margin:0;font-size:1rem;line-height:1.3;font-weight:650}
h1 a{color:var(--fg);text-decoration:none}
.bar nav a{color:var(--muted);font-size:.9375rem;text-decoration:none;padding:8px 0}
.bar nav a:hover,.bar nav a[aria-current]{color:var(--fg)}
.wrap{flex:1;padding-block:24px 40px;overflow-wrap:anywhere}
h2{margin:1.75em 0 .5em;font-size:1.125rem;line-height:1.35;font-weight:650;text-wrap:balance}
h3{margin:1.25em 0 .4em;font-size:1.0625rem;line-height:1.45;font-weight:600;text-wrap:balance}
h4{margin:1.4em 0 .3em;font-size:.9375rem;line-height:1.4;font-weight:600;color:var(--muted)}
h5{margin:1em 0 .25em;font-size:.875rem;font-weight:600;color:var(--muted)}
.wrap>h2:first-child,.back+h2,.a>h2:first-child,.empty>h2:first-child{margin-top:0}
.back+h2,.empty>h2{font-size:1.375rem;line-height:1.3}
p,ol,ul{margin:.6em 0}
ol,ul{padding-left:1.5em}
li{margin:.45em 0;padding-left:.2em}
li::marker{color:var(--muted);font-variant-numeric:tabular-nums}
.wrap p,.wrap li{white-space:pre-line;hyphens:auto}
em{color:var(--muted)}
.n-src,.n-meta{color:var(--muted);font-size:.875rem;line-height:1.5}
.n-src{margin-top:1.25em}
.n-src+.n-src,.n-meta+.n-meta,.n-src+.n-meta{margin-top:.3em}
p.n-limit,p.n-gap,p.n-fix{padding:.6em .85em;border-radius:8px}
p.n-limit{background:var(--limit);border:1px solid var(--limit-line);font-size:.9375rem}
p.n-gap,p.n-fix{background:var(--gap)}
a.ref{margin-left:.1em;padding:.05em .5em;border-radius:6px;background:var(--accent-soft);color:var(--accent);
 font-size:.8125rem;line-height:1.5;font-weight:500;text-decoration:none;font-variant-numeric:tabular-nums;
 -webkit-box-decoration-break:clone;box-decoration-break:clone;hyphens:none}
a.ref:hover{background:var(--accent);color:var(--accent-ink)}
a.ref.same{display:none}
.dialog{flex:1}
.empty{padding:8px 0 24px;max-width:38rem}
.empty p{color:var(--muted)}
.tools{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:4px 20px;margin:-8px 0 8px}
.tools form{margin:0}
.turn{padding:28px 0;border-top:1px solid var(--line);scroll-margin-top:12px}
.turn:first-child,.tools+.turn{border-top:0;padding-top:4px}
.wrap.chat{display:flex;flex-direction:column}
.q{width:fit-content;max-width:min(34rem,88%);margin-left:auto;padding:.6rem .95rem;border-radius:14px 14px 4px 14px;
 background:var(--q);font-weight:550;line-height:1.5;white-space:pre-line}
.as{max-width:min(34rem,88%);margin:.4rem 0 0 auto;color:var(--muted);font-size:.875rem;text-align:right}
.a{margin-top:1.1rem;font-size:1.0625rem;line-height:1.65}
.a h2:first-child{font-size:.9375rem;color:var(--muted);font-weight:600}
.a h3{font-size:1.125rem}
.after{margin:1rem 0 0;color:var(--muted);font-size:.875rem}
input,button{font:inherit}
input[type=text],input:not([type]),input[type=date]{min-height:44px;padding:.55rem .8rem;border:1px solid var(--line-strong);border-radius:10px;
 background:var(--surface);color:var(--fg)}
input:hover{border-color:var(--muted)}
input:focus-visible{outline:2px solid var(--accent);outline-offset:1px;border-color:var(--accent)}
::placeholder{color:var(--muted);opacity:1}
button{min-height:44px;padding:0 1.15rem;border:0;border-radius:10px;background:var(--accent);color:var(--accent-ink);font-weight:600;cursor:pointer}
button:hover{background:var(--accent-hover)}
button:disabled{opacity:.7;cursor:progress}
button.secondary{min-height:36px;padding:4px 0;background:none;color:var(--muted);font-size:.875rem;font-weight:500;
 text-decoration:underline;text-decoration-color:var(--line-strong);text-underline-offset:.25em}
button.secondary:hover{color:var(--fg);text-decoration-color:currentColor}
form.ask{position:sticky;bottom:0;z-index:1;display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:8px;align-items:center;
 margin:16px -16px 0;padding:12px 16px calc(12px + env(safe-area-inset-bottom));background:var(--bg);
 border-top:1px solid var(--line);box-shadow:0 -8px 16px -14px var(--shadow)}
form.ask input[name=question]{width:100%;min-width:0}
.date{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:.875rem;white-space:nowrap}
.date input{font-size:.875rem;padding-inline:.5rem}
.meta{margin:12px 0 0;color:var(--muted);font-size:.8125rem;line-height:1.5}
form.plain{display:flex;flex-wrap:wrap;align-items:flex-end;gap:12px;margin:0 0 24px;padding:16px;
 background:var(--surface);border:1px solid var(--line);border-radius:12px}
form.plain label{display:flex;flex-direction:column;gap:4px;color:var(--muted);font-size:.875rem}
form.plain label:first-child{flex:1 1 18rem}
form.plain input{color:var(--fg);font-size:1rem}
.back{margin:0 0 20px;font-size:.9375rem}
.ver{color:var(--muted);font-size:.8125rem;line-height:1.5}
.frag div,.frag>p:not(.back):not(.ver){padding:.85rem 0;border-top:1px solid var(--line);margin:0}
.frag div p{margin:0}
.frag div p+p{margin-top:.4em;color:var(--muted);font-size:.875rem}
.frag strong{display:block;margin-bottom:.15rem;color:var(--muted);font-size:.75rem;font-weight:500;font-variant-numeric:tabular-nums}
.frag #selected{margin-inline:-12px;padding-inline:12px;border:1px solid var(--line-strong);border-radius:10px;background:var(--surface)}
.frag #selected+div,.frag #selected+p{border-top-color:transparent}
.frag #selected strong{color:var(--accent)}
mark{padding:0 .1em;border-radius:3px;background:var(--mark);color:inherit;-webkit-box-decoration-break:clone;box-decoration-break:clone}
.lead{color:var(--muted)}
ol.docs{list-style:none;padding:0;margin:20px 0 0}
ol.docs li{margin:0;padding:.85rem 0;border-top:1px solid var(--line)}
a.doc{font-weight:600;text-decoration:none}
a.doc:hover{text-decoration:underline}
.doc-meta{display:block;margin-top:.15rem;color:var(--muted);font-size:.875rem}
@media (max-width:600px){
 .wrap{padding-block:16px 32px}
 form.ask{grid-template-columns:minmax(0,1fr) auto}
 form.ask input[name=question]{grid-column:1/-1}
 .q,.as{max-width:94%}
 .a{font-size:1rem}
}
.quotes{margin:.25rem 0 1rem}
.quote{padding:.6rem 0;border-top:1px solid var(--line)}
.quote-ref{font-size:.8rem;color:var(--muted)}
.quote blockquote{margin:.3rem 0 0;padding:.1rem 0 .1rem .8rem;border-left:3px solid var(--line-strong);white-space:pre-line}
.doclinks{font-size:.9rem;margin:.2rem 0 .8rem}
.doc-meta .orig{margin-left:.4rem}
.as form.again{display:inline;margin-left:.6rem}
.as form.again button{min-height:0;padding:0;font-size:.8125rem}
p.n-fail{padding:.6em .85em;border-radius:8px;background:var(--limit);border:1px solid var(--limit-line);font-weight:500}
'''
STYLE = '<style>' + re.sub(r'\s*\n\s*', '', CSS.strip()) + '</style>'

# Service lines of the answers (written by the renderers as the start of a <p>/<li>) get a
# class for styling; the text and the elements themselves stay as rendered.
SERVICE_CLASSES = (('Источник:', 'n-src'), ('Ограничение:', 'n-limit'), ('Недостаточно данных', 'n-gap'),
                   ('Вопрос не относится', 'n-gap'), ('Применимость', 'n-meta'), ('Самопроверка', 'n-meta'),
                   ('Поправка к вопросу', 'n-fix'))
SERVICE_LINE = re.compile(r'<(p|li)>(' + '|'.join(re.escape(prefix) for prefix, _ in SERVICE_CLASSES) + ')')
# A citation as cite() writes it (href and the full fragment ID as text); the page shows
# a readable label instead and keeps the ID in the title.
CITATION = re.compile(r'<a href="(/fragment\?id=[^"]*)">([^<]*)</a>')
POINT = re.compile(r'\s*(\d{1,3}(?:\.\d{1,3})*)\.(?=\s|$)')     # «3.1. Текст» → 3.1
SMALL_WORDS = {'о', 'об', 'по', 'и', 'в', 'во', 'на', 'для', 'с', 'со', 'к', 'от', 'при'}

def ref_label(fragment_id, text=None, unit=None, cell=False):
    """The cited place in words: «стр. 2», «п. 3.1», «абзац 4», «таблица, строка 2»,
    «лист «Справочник», строка 5». ``text`` is the fragment text, ``unit`` the cited part
    of it; a point number at their start names the place more precisely."""
    locator = fragment_id.split('#', 1)[1] if '#' in fragment_id else ''
    parts = locator.split('/')
    point = POINT.match(unit or '') or (POINT.match(text or '') if parts[0] == 'body' else None)
    if parts[0] == 'page' and len(parts) > 1 and parts[1].isdigit():
        return 'стр. ' + parts[1] + (', п. ' + point.group(1) if point else '')
    if parts[0] == 'sheet' and len(parts) >= 4 and parts[-2] == 'cell':
        row = re.sub(r'^[A-Za-z]+', '', parts[-1])
        place = ('ячейка ' + parts[-1]) if cell or not row.isdigit() else ('строка ' + row)
        return 'лист «' + parts[1] + '», ' + place
    if 'table' in parts and 'row' in parts[:-1]:
        return 'таблица, строка ' + parts[parts.index('row') + 1]
    if point:
        return 'п. ' + point.group(1)
    if parts[0] == 'body' and len(parts) > 1 and parts[1].isdigit():
        return 'абзац ' + parts[1]
    if not locator and 0 < len(fragment_id) <= 24:
        return fragment_id                      # fixture IDs are short and readable
    return 'фрагмент'

def document_info(db, source_id, version, cache=None):
    """Human names of an imported version: title, short name, requisites label, file name."""
    key = (source_id, version)
    if cache is not None and key in cache:
        return cache[key]
    row = db.execute('SELECT filename FROM imports WHERE source_id=? AND version=?', key).fetchone()
    filename = row['filename'] if row else ''
    first = db.execute('SELECT id, locator, text FROM import_fragments WHERE source_id=? AND version=? ORDER BY rowid LIMIT 2',
                       key).fetchall()
    from .requisites import requisites, label
    try:
        data = requisites(db, source_id, version)
    except (sqlite3.Error, ValueError, KeyError, TypeError):
        data = {}
    title = document_title(filename, first)
    stem = re.sub(r'\.[A-Za-z0-9]{2,5}$', '', filename) or filename
    if first and first[0]['locator'].startswith('sheet/'):
        short = stem
    elif re.match(r'Об?\s', title) and data.get('kind') and data.get('number'):
        short = data['kind'].capitalize() + ' № ' + data['number']     # «Об утверждении…» → «Приказ № 41-1»
    else:
        words = re.sub(r'\s*\([^)]*\)', '', title).split()[:4]
        while len(words) > 1 and words[-1].lower() in SMALL_WORDS:
            words.pop()
        short = ' '.join(words) or stem
    info = {'title': title, 'short': short, 'label': label(data) if data else None, 'filename': filename,
            'first': first[0]['id'] if first else None}
    if cache is not None:
        cache[key] = info
    return info

def label_citations(content, db=None):
    """Readable link text for the citations of one answer. With citations of more than
    one document each label starts with the document's short name. Several cells of one
    sheet row are one place: the repeated links stay (as hidden duplicates)."""
    found = CITATION.findall(content)
    if not found:
        return content
    rows, cache = {}, {}
    if db is not None:
        try:
            ids = list(dict.fromkeys(html.unescape(text) for _, text in found))
            for chunk in range(0, len(ids), 500):
                part = ids[chunk:chunk + 500]
                for r in db.execute('SELECT id, source_id, version, text FROM import_fragments WHERE id IN (%s)'
                                    % ','.join('?' * len(part)), part):
                    rows[r['id']] = r
        except sqlite3.Error:
            rows = {}
    several = len({r['source_id'] for r in rows.values()}) > 1
    previous = {'end': -1, 'label': None}
    def citation(m):
        href, fid = m.group(1), html.unescape(m.group(2))
        row = rows.get(fid)
        unit = None
        at = re.search(r'&amp;at=(\d+)-(\d+)', href)
        if row and at and int(at.group(1)) < int(at.group(2)) <= len(row['text']):
            unit = row['text'][int(at.group(1)):int(at.group(2))]
        label = ref_label(fid, row['text'] if row else None, unit)
        if several and row:
            try:
                label = document_info(db, row['source_id'], row['version'], cache)['short'] + ', ' + label
            except sqlite3.Error:
                pass
        same = label == previous['label'] and not content[previous['end']:m.start()].strip()
        previous.update(end=m.end(), label=label)
        return ('<a href="' + href + '" class="ref' + (' same' if same else '') + '" title="' + m.group(2) + '">'
                + esc(label) + '</a>')
    return CITATION.sub(citation, content)

def decorate(content, db=None):
    """Classes for the service lines and readable citation labels."""
    def service(m):
        cls = next(c for prefix, c in SERVICE_CLASSES if prefix == m.group(2))
        return '<' + m.group(1) + ' class="' + cls + '">' + m.group(2)
    return label_citations(SERVICE_LINE.sub(service, content), db)

DB = os.environ.get('NAVIGATOR_DB', DEFAULT_DB)

def esc(value):
    return html.escape(str(value), quote=True)

def import_notes(metadata):
    notes = []
    if metadata.get('unreadable'): notes.append('OCR не распознал текст; требуется визуальная проверка оригинала.')
    if metadata.get('formula_error'): notes.append('Формула содержит повреждённую ссылку; результат не подтверждён.')
    if metadata.get('requires_visual_review') and not metadata.get('unreadable'): notes.append('OCR требует визуальной сверки оригинала.')
    if metadata.get('type') == 'pdf_text' and metadata.get('ocr_needed'): notes.append('Текстовый слой может быть неполным; требуется сверка оригинала.')
    return notes

def fragment_href(fragment_id, item=None):
    """Link to an imported fragment; a unit inside it is passed as a character range."""
    href = '/fragment?id=' + quote(fragment_id, safe='')
    if item is not None and isinstance(item.get('start'), int) and isinstance(item.get('end'), int) and item['end'] > item['start']:
        href += '&amp;at=' + str(item['start']) + '-' + str(item['end'])
    return href

def document_href(source_id, version=None, fragment_id=None):
    """The whole imported document (its text in reading order), optionally at one fragment."""
    href = '/document?source=' + quote(source_id, safe='')
    if version:
        href += '&amp;version=' + quote(version, safe='')
    if fragment_id:
        href += '&amp;at=' + quote(fragment_id, safe='') + '#selected'
    return href

def original_href(source_id, version):
    return '/original?source=' + quote(source_id, safe='') + '&amp;version=' + quote(version, safe='')

# Original files are served as they were imported: PDF opens in the browser, the rest downloads.
ORIGINAL_TYPES = {'.pdf': ('application/pdf', 'inline', 'PDF'),
                  '.docx': ('application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'attachment', 'DOCX'),
                  '.xlsx': ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'attachment', 'XLSX')}

def document_links(db, source_id, version):
    """«Весь документ · Исходный файл (PDF, 120 КБ)» for an imported version."""
    row = db.execute('SELECT filename, length(original) AS size FROM imports WHERE source_id=? AND version=?',
                     (source_id, version)).fetchone()
    links = ['<a href="' + document_href(source_id, version) + '">Весь документ</a>']
    if row is not None and row['size']:
        kind = ORIGINAL_TYPES.get(os.path.splitext(row['filename'] or '')[1].lower(), (None, None, 'файл'))[2]
        size = max(1, round(row['size'] / 1024))
        links.append('<a href="' + original_href(source_id, version) + '">Исходный файл (' + kind + ', ' + str(size) + ' КБ)</a>')
    return '<p class="doclinks">' + ' · '.join(links) + '</p>'

def cite(fragment_id, item=None):
    return ' <a href="' + fragment_href(fragment_id, item) + '">' + esc(fragment_id) + '</a>'

def highlighted(text, at):
    if at:
        start, end = at
        if 0 <= start < end <= len(text):
            return esc(text[:start]) + '<mark id="cited">' + esc(text[start:end]) + '</mark>' + esc(text[end:])
    return esc(text)

def render_import_fragment(row, selected, at=None):
    import json
    metadata = json.loads(row['metadata'])
    headers = ''
    for key, label in (('column_headers','Заголовки столбца'), ('row_headers','Заголовки строки')):
        if metadata.get(key):
            headers += '<p>' + label + ': ' + esc('; '.join(x['coordinate'] + ': ' + x['text'] for x in metadata[key])) + '</p>'
    if metadata.get('header_inferred') and headers:
        headers += '<p>Заголовки определены предположительно; требуется сверка.</p>'
    if metadata.get('formula') is not None:
        headers += '<p>Формула: ' + esc(metadata['formula']) + '; сохранённое значение: ' + esc(metadata.get('cached_value') if metadata.get('cached_value') is not None else 'отсутствует') + '</p>'
    place = ref_label('#' + row['locator'], row['text'], cell=True)
    return '<div' + (' id="selected"' if selected else '') + '><p><strong title="' + esc(row['locator']) + '">' + esc(place) + '</strong> ' + highlighted(row['text'], at if selected else None) + ''.join(' <em>' + esc(note) + '</em>' for note in import_notes(metadata)) + '</p>' + headers + '</div>'

STAGE_HEADINGS = (('До поездки или события', 'до'), ('После возвращения или события', 'после'))

def render_stages(items, other_heading, link):
    """Group staged units; conditions and exceptions are shown inside their stage."""
    content = ''
    for heading, key in STAGE_HEADINGS + ((other_heading, None),):
        group = [item for item in items if item.get('stage') == key]
        if not group: continue
        content += '<h4>' + heading + '</h4>'
        steps = [item for item in group if not item.get('condition')]
        conditions = [item for item in group if item.get('condition')]
        if steps:
            content += '<ol>' + ''.join('<li>' + esc(item['text']) + link(item['basis'], item) + '</li>' for item in steps) + '</ol>'
        if conditions:
            content += '<h5>Условия и исключения этого этапа</h5><ul>' + ''.join('<li>' + esc(item['text']) + link(item['basis'], item) + '</li>' for item in conditions) + '</ul>'
    return content

SUPPORT_HEADINGS = (('channel', 'Куда обращаться'), ('details', 'Что указать в обращении'),
                    ('time', 'Сроки рассмотрения'), ('scope', 'Ограничения из текста документа'))

def render_support(support, import_meta, source_notes):
    def link(fid, item=None):
        return ''.join(' <em>' + esc(note) + '</em>' for note in import_notes(import_meta.get(fid, {}))) + cite(fid, item)
    content = '<h2>Найденные сведения об обращении</h2><p>Применимость и полнота не проверены; дата вступления и связь редакций не подтверждены. Сверьте источник.</p>'
    for note in support['notes']:
        content += '<p>Ограничение: ' + esc(note) + '</p>'
    many = len(support['sources']) > 1
    for number, source in enumerate(support['sources'], 1):
        if many: content += '<h3>Документ ' + str(number) + ': ' + esc(source['title']) + '</h3>'
        for key, heading in SUPPORT_HEADINGS:
            units = source['facets'][key]
            if units:
                content += '<h4>' + heading + '</h4><ol>' + ''.join('<li>' + esc(u['text']) + link(u['id'], u) + '</li>' for u in units) + '</ol>'
        for note in source.get('limits', []):
            content += '<p>Ограничение: ' + esc(note) + '</p>'
        for gap in source['missing']:
            content += '<p>Недостаточно данных для части' + (' (' + esc(source['title']) + ')' if many else '') + ': ' + esc(gap) + '.</p>'
    for extra in support['extra']:
        content += '<h3>' + esc(extra['part']) + '</h3>'
        if extra['items']:
            content += '<ol>' + ''.join('<li>' + esc(i['text']) + link(i['id'], i) + '</li>' for i in extra['items']) + '</ol>'
    for gap in support['gaps']:
        content += '<p>Недостаточно данных для части вопроса: ' + esc(gap) + '.</p>'
    for note, fid in source_notes:
        content += '<p>Ограничение: ' + esc(note) + ' <a href="/fragment?id=' + quote(fid, safe='') + '">' + esc(fid) + '</a></p>'
    return content

# A question about what to do, in what order: its answer is a numbered list of steps.
PROCEDURE = re.compile(r'\bкак\s+(?:мне\s+)?(?:оформ|получ|пода|подключ|заказ|заброн|сда|восстанов|согласов|сдела|отправ|зарегистр|попаст|обрат)'
                       r'|\bчто\s+(?:мне\s+)?(?:нужно|надо|необходимо|следует)\s+(?:с)?делать|\bчто\s+делать|\bпоряд(?:ок|ке)\b'
                       r'|\bшаг|\bпоследовательност|\bпроцедур|\bалгоритм', re.I)


def render_rag(db, result, when, question=''):
    """HTML of a verified RAG answer; every statement links to its chunk address.

    The statements are a numbered list only when they are steps the employee takes (a question
    about how to do something); otherwise they read as text, one paragraph per statement."""
    status = result['status']
    if status == 'off_topic':
        return ('<p>Вопрос не относится к внутренним документам и регламентам организации; '
                'навигатор отвечает только по ним.</p>')
    if status == 'insufficient' or not result['items']:
        return '<p>Недостаточно данных для ответа в доступных материалах.</p>'
    content = '<h2>Ответ</h2>'
    if result['short_answer'] and not (len(result['items']) == 1 and result['short_answer'].strip(' .') == result['items'][0]['text'].strip(' .')):
        content += '<h3>' + esc(result['short_answer']) + '</h3>'
    steps = bool(PROCEDURE.search(question or '')) and len(result['items']) > 1
    content += '<ol class="steps">' if steps else '<div class="answer-text">'
    def links(chunk):
        # A sheet row is several cell fragments: cite every cell, not only the first.
        cells = (chunk.get('meta') or {}).get('cells')
        if cells:
            return ''.join(cite(cid) for cid in cells)
        return cite(chunk['fragment_id'], {'start': chunk['start'], 'end': chunk['end']})
    for item in result['items']:
        # A false premise of the question: the document's own sentence, quoted (rag.premise_corrections).
        text = ('Поправка к вопросу. В документе: «' + item['text'] + '»') if item.get('premise') else item['text']
        tag = 'li' if steps else 'p'
        content += '<' + tag + '>' + esc(text) + ''.join(links(c) for c in item['chunks']) + '</' + tag + '>'
    content += '</ol>' if steps else '</div>'
    if result.get('truncated'):
        # The reply was cut off at the model's output limit: only its whole, checked statements are shown.
        content += ('<p class="n-fail">Ответ оказался длиннее, чем модель выдаёт за один раз: показана его '
                    'проверенная часть. Уточните вопрос, чтобы получить остальное.</p>')
    # No «Недостаточно данных для части вопроса» line from the model's status: the model declares
    # «partial» on nearly every answer, so the line stood under full answers too, and its free text
    # contradicted them (external audit of d0bba9b). What the code itself could not confirm is said
    # by the «Самопроверка» line below.
    # Then the fragments the answer rests on, verbatim — what a reader checks the answer against.
    # Blocks, not <p>/<li>: they are quotes of the documents, not statements of the answer.
    chunks = list({c['id']: c for item in result['items'] for c in item['chunks']}.values())
    if chunks:
        content += '<h2>Фрагменты документов, на которых основан ответ</h2><div class="quotes">'
        for c in chunks:
            text = c['text'] if len(c['text']) <= 900 else c['text'][:900].rsplit(' ', 1)[0] + ' …'
            content += ('<div class="quote"><div class="quote-ref">' + links(c).strip()
                        + ' · <a class="doclink" href="' + document_href(c['source_id'], c['version'], c['fragment_id']) + '">в документе</a>'
                        + '</div><blockquote>' + esc(text) + '</blockquote></div>')
        content += '</div>'
    # Last: where it comes from, what limits it, how it was checked.
    content += '<h2>Источники и ограничения</h2>'
    keys = list(dict.fromkeys((c['source_id'], c['version']) for item in result['items'] for c in item['chunks']))
    for key in keys:
        if result['headers'].get(key):
            # The heading also carries a hint for the model («…: действующий текст изменённых пунктов —
            # во фрагментах этого приказа»); a reader needs only «изменён приказом № … с …».
            shown = re.sub(r'(изменён приказом[^:·]*):[^·]*', r'\1', result['headers'][key])
            content += ('<p>Источник: ' + esc(shown) + '. <a class="doclink" href="' + document_href(*key)
                        + '">Открыть документ</a></p>')
    from .requisites import status as version_status
    for _, (state, notes) in version_status(db, keys, when).items():
        for note, fid in notes:
            content += '<p>Ограничение: ' + esc(note) + (cite(fid) if fid else '') + '</p>'
    if result['dropped']:
        content += ('<p>Самопроверка: ' + str(len(result['dropped'])) + ' утвержд. модели не подтверждены текстом '
                    'источников и не показаны.</p>')
    content += ('<p>Применимость и полнота проверены по тексту найденных документов; ответ сформирован локальной '
                'моделью и проверен по цитатам; сверьте источник.</p>')
    return content

# The latest imported version of each source that has text to search.
LATEST_IMPORTS = ('SELECT i.source_id, i.version, i.filename FROM imports i '
                  'WHERE i.rowid IN (SELECT MAX(rowid) FROM imports GROUP BY source_id) '
                  'AND EXISTS (SELECT 1 FROM import_fragments f WHERE f.source_id=i.source_id AND f.version=i.version)')
HEADING_ONLY = re.compile(r'[А-ЯЁA-Z]{3,}\s+(?:от|№)\s')   # «ПРИКАЗ от 20.06.2022 № 77-3»: the title follows

def has_imports(db):
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {'imports', 'import_fragments'} <= names

def document_count(db):
    return len(db.execute(LATEST_IMPORTS).fetchall()) if has_imports(db) else 0

def document_title(filename, first):
    """The heading line of a document as written in it; a table by its sheet name; else the file name."""
    if not first:
        return filename
    if first[0]['locator'].startswith('sheet/'):
        sheet = first[0]['locator'].split('/')[1]
        return filename if len(sheet) < 3 or re.fullmatch(r'(?i)(лист|sheet)\s*\d*', sheet) else sheet
    line = first[0]['text'].strip().split('\n', 1)[0].strip()
    if HEADING_ONLY.match(line) and len(first) > 1 and first[1]['locator'].startswith('body/'):
        line = first[1]['text'].strip().split('\n', 1)[0].strip()
    # «ПОРЯДОК закупок…» → «Порядок закупок…»; abbreviations alone are kept.
    line = re.sub(r'^([А-ЯЁ])([А-ЯЁ]{2,})(?=\s+[а-яё])', lambda m: m.group(1) + m.group(2).lower(), line)
    if len(line) < 4:
        return filename
    return line if len(line) <= 140 else line[:140].rsplit(' ', 1)[0] + '…'

def imported_documents(db):
    """Searchable documents: title, file name, requisites label and the first fragment."""
    if not has_imports(db):
        return []
    docs = []
    for row in db.execute(LATEST_IMPORTS).fetchall():
        info = document_info(db, row['source_id'], row['version'])
        size = db.execute('SELECT length(original) FROM imports WHERE source_id=? AND version=?',
                          (row['source_id'], row['version'])).fetchone()[0]
        docs.append(dict(info, fragment=info['first'], source_id=row['source_id'], version=row['version'], size=size))
    return sorted(docs, key=lambda d: d['title'].casefold())

def source_heading(db, row, at=None):
    """Heading of the source page: the document in words, the selected place, no hashes.
    The version and locator stay in data attributes for checks."""
    info = document_info(db, row['source_id'], row['version'])
    unit = row['text'][at[0]:at[1]] if at and 0 <= at[0] < at[1] <= len(row['text']) else None
    meta = [info['label'], info['filename'] if info['filename'] != info['title'] else None,
            'выбран: ' + ref_label('#' + row['locator'], row['text'], unit, cell=True)]
    return ('<h2>' + esc(info['title']) + '</h2><p class="ver" data-version="' + esc(row['version']) + '" data-locator="'
            + esc(row['locator']) + '">' + esc(' · '.join(x for x in meta if x)) + '</p>'
            + document_links(db, row['source_id'], row['version']))

def render_documents(docs):
    content = '<h2>Документы в базе</h2>'
    if not docs:
        return content + '<p>Документы ещё не загружены.</p>'
    content += ('<p class="lead">Навигатор ищет ответы только в этих документах (' + str(len(docs)) + '). '
                'Название открывает текст документа целиком; исходный файл — в том виде, в каком он загружен.</p><ol class="docs">')
    for d in docs:
        meta = [d['label'], d['filename'] if d['filename'] != d['title'] else None]
        original = ''
        if d.get('size'):
            kind = ORIGINAL_TYPES.get(os.path.splitext(d['filename'] or '')[1].lower(), (None, None, 'файл'))[2]
            original = (' <a class="orig" href="' + original_href(d['source_id'], d['version']) + '">исходный файл ('
                        + kind + ', ' + str(max(1, round(d['size'] / 1024))) + ' КБ)</a>')
        content += ('<li><a class="doc" href="' + document_href(d['source_id'], d['version']) + '">' + esc(d['title']) + '</a>'
                    + ('<span class="doc-meta">' + esc(' · '.join(x for x in meta if x)) + original + '</span>'
                       if any(meta) or original else '') + '</li>')
    return content + '</ol>'


def understood(turn, by_n):
    """«Понял вопрос как …» under a follow-up: what it was taken to mean, which earlier question
    it was tied to (a link), the date carried over, and a way to ask it as a new question."""
    relation = turn.get('relation') or 'new'
    if not relation.startswith('follow'):
        return ''
    number = turn.get('ref')
    ref = by_n.get(number)
    to = f'<a href="#t{number}">вопросу {number}</a>' if ref else 'предыдущему вопросу'
    if relation.startswith('follow-joined'):
        earlier = (ref or {}).get('standalone') or (ref or {}).get('question') or ''
        line = 'Понял как уточнение к ' + to + (' «' + esc(earlier) + '»' if earlier else '')
    elif turn['standalone'] != turn['question']:
        line = 'Понял вопрос как: «' + esc(turn['standalone']) + '» — уточнение к ' + to
    else:
        line = 'Понял как уточнение к ' + to
    if relation.endswith('+date') and turn.get('asked_on'):
        day = turn['asked_on']
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
            day = '.'.join(reversed(day.split('-')))
        line += '; на дату ' + esc(day) + ', как в нём'
    again = ('<form method="post" action="/ask" class="again"><input type="hidden" name="question" value="'
             + esc(turn['question']) + '"><input type="hidden" name="fresh" value="1">'
             '<button class="secondary">Это новый вопрос</button></form>')
    return '<div class="as">' + line + '.' + again + '</div>'


BACK = '<p class="back"><a href="/">Вернуться к вопросам</a></p>'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Questions and document text are not logged.

    captured = None      # a list while a dialogue turn collects its answer instead of sending it

    notice = None        # «no answer was generated, because …», shown right after the question form

    def page(self, content, status=200, headers=(), kind='', title='Навигатор сотрудника'):
        if self.notice:
            note, self.notice = '<p class="n-fail">' + esc(self.notice) + '</p>', None
            at = content.find('</form>')
            content = content[:at + 7] + note + content[at + 7:] if at >= 0 else note + content
        if self.captured is not None:
            self.captured.append((content, status))
            return
        current = ' aria-current="page"' if kind == 'docs' else ''
        body = ('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
                '<meta name="color-scheme" content="light dark"><title>' + esc(title) + '</title>' + STYLE
                + '<body><header class="top"><div class="bar"><h1><a href="/">Навигатор сотрудника</a></h1>'
                '<nav><a href="/documents"' + current + '>Документы в базе</a></nav></div></header>'
                '<main class="wrap' + (' ' + kind if kind else '') + '">' + self.labelled(content) + '</main></body></html>').encode()
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        # Pages carry the person's questions and answers (chat history): never kept by a cache.
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def labelled(content):
        if not CITATION.search(content):
            return decorate(content)
        try:
            with closing(connect_existing(DB)) as db:
                return decorate(content, db)
        except (sqlite3.Error, OSError):
            return decorate(content)

    def form(self, question='', chat=False):
        if chat:
            return ('<form method="post" action="/ask" class="ask"><input name="question" maxlength="2000" required '
                    'autocomplete="off" enterkeyhint="send" placeholder="Вопрос по документам организации" '
                    'aria-label="Вопрос"><label class="date" title="Необязательно: дата, на которую нужен ответ">'
                    'На дату <input type="date" name="date"></label><button>Спросить</button></form>')
        return '<form method="post" action="/ask" class="plain"><label>Вопрос <input name="question" maxlength="2000" value="' + esc(question) + '"></label> <label>Дата <input type="date" name="date"></label> <label>Категория <input name="category"></label><button>Найти</button></form>'

    def fragment_page(self, content):
        """A source with the cited place; the optional script brings it into view."""
        return self.page(BACK + content + '<script>var s=document.getElementById("cited")||document.getElementById("selected");'
                         'if(s&&!location.hash)s.scrollIntoView({block:"center"});</script>', kind='frag', title='Источник')

    def redirect(self, location, headers=()):
        self.send_response(303)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header('Location', location)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def cookie(self, conversation):
        return ('Set-Cookie', f'{history.COOKIE}={conversation}; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000')

    def chat_page(self):
        """The dialogue of this browser: earlier questions with their answers, then the form."""
        conversation = history.from_cookie(self.headers.get('Cookie'))
        headers = ()
        if conversation is None:
            conversation = history.new_id()
            headers = (self.cookie(conversation),)
        count = None
        try:
            with closing(connect(DB)) as db, db:
                turns = history.turns(db, conversation)
                count = document_count(db)
                for t in turns:
                    t['answer_html'] = decorate(t['answer_html'], db)
        except (sqlite3.Error, OSError):
            turns = []
        docs_link = '<a href="/documents">Документы в базе</a>'
        loaded = 'загруженным документам' + (f' ({count})' if count is not None else '')
        content = '<div class="dialog">'
        if not turns:
            content += ('<div class="empty"><h2>Спросите о правилах и порядках организации</h2>'
                        '<p>Ответ строится только по загруженным документам, у каждого утверждения есть ссылка '
                        'на пункт источника. Если в документах ответа нет, навигатор так и скажет.</p>'
                        '<p>Уточнять можно в продолжение: «а для бухгалтерии?», «а где их кабинет?».</p>'
                        f'<p>Поиск идёт только по {loaded}: {docs_link}.</p></div>')
        else:
            content += ('<div class="tools"><form method="post" action="/new"><button class="secondary">Новый диалог</button></form>'
                        '<form method="post" action="/clear"><button class="secondary">Очистить историю</button></form></div>')
        by_n = {t['n']: t for t in turns}
        for t in turns:
            content += f'<section class="turn" id="t{t["n"]}"><div class="q">{esc(t["question"])}</div>'
            content += understood(t, by_n)
            content += f'<div class="a">{t["answer_html"]}</div>'
            if 'Недостаточно данных для ответа' in t['answer_html']:
                content += (f'<p class="after">Ответ ищется только в загруженных документах. Посмотрите '
                            f'<a href="/documents">список документов</a> или переформулируйте вопрос.</p>')
            content += '</section>'
        content += '</div>' + self.form(chat=True)
        # With an empty dialogue the same line is already in the hint above.
        content += ('<p class="meta">' + (f'Поиск идёт только по {loaded}: {docs_link}. ' if turns else '')
                    + 'История диалога хранится на этом сервере и никуда не передаётся.</p>')
        # Optional: the page works without it (scroll to the last answer, focus, busy button).
        content += ('<script>var t=document.querySelectorAll(".turn"),f=document.querySelector("form.ask"),'
                    'b=f.querySelector("button"),i=f.querySelector("input[name=question]");'
                    'if(t.length&&!location.hash)t[t.length-1].scrollIntoView();'
                    'if(matchMedia("(min-width:700px)").matches)i.focus({preventScroll:true});'
                    'f.addEventListener("submit",function(){b.disabled=true;b.textContent="Ищу в документах…"});'
                    'addEventListener("pageshow",function(){b.disabled=false;b.textContent="Спросить"});</script>')
        return self.page(content, headers=headers, kind='chat')

    def chat_post(self, conversation, fields):
        """One dialogue turn: rewrite a follow-up into a self-contained question, answer it as
        a single question (same pipeline and checks), keep the turn, show the dialogue."""
        question = fields.get('question', [''])[0]
        asked_on = fields.get('date', [''])[0] or None
        from . import rag
        try:
            with closing(connect(DB)) as db, db:
                turns = history.session(db, conversation)
        except (sqlite3.Error, OSError):
            turns = []
        # How the question relates to the dialogue is decided by code (history.relation);
        # only a question that refers back is rewritten by the model.
        # «Это новый вопрос» (fresh=1): the person said the question does not refer to the dialogue.
        relation = 'new' if fields.get('fresh', [''])[0] == '1' else history.relation(question, turns)
        standalone, ref = question, None
        if relation == 'follow':
            ref = turns[-1]['n']
            model = rag.llm() if rag.enabled() else None
            standalone = rag.condense(model, turns, question) if model else question
            previous = turns[-1].get('standalone') or turns[-1]['question']
            if standalone != question and standalone.startswith(previous.strip() + ' '):
                relation = 'follow-joined'   # the model did not rewrite it: the previous question went in front
            if not asked_on and turns[-1].get('asked_on') and not history.NOW.search(question):
                # «А раньше?» after a question on 01.06.2024: the date carries over.
                asked_on, relation = turns[-1]['asked_on'], relation + '+date'
        self.captured = []
        try:
            self.answer_post(dict(fields, question=[standalone], date=[asked_on or '']))
        finally:
            captured, self.captured = self.captured, None
        content, status = captured[-1] if captured else ('<p>Технический сбой. Повторите попытку.</p>', 503)
        prefix = self.form(standalone)
        answer = content[len(prefix):] if content.startswith(prefix) else content
        if status < 500:
            with closing(connect(DB)) as db, db:
                n = history.add(db, conversation, question, standalone, history.plain(answer), answer,
                                asked_on, relation, ref)
            return self.redirect(f'/#t{n}')
        return self.page(self.form(chat=True) + answer, status)

    def document_page(self, db, row, at=''):
        """The whole imported document in reading order: every page, paragraph or table row,
        labelled as in citations; the fragment «at» is highlighted and scrolled to."""
        info = document_info(db, row['source_id'], row['version'])
        meta = [info['label'], info['filename'] if info['filename'] != info['title'] else None]
        content = ('<h2>' + esc(info['title']) + '</h2><p class="ver" data-version="' + esc(row['version']) + '">'
                   + esc(' · '.join(x for x in meta if x)) + '</p>' + document_links(db, row['source_id'], row['version']))
        fragments = db.execute('SELECT * FROM import_fragments WHERE source_id=? AND version=? ORDER BY rowid',
                               (row['source_id'], row['version'])).fetchall()
        if not fragments:
            content += '<p>Текст документа не извлечён; откройте исходный файл.</p>'
        content += ''.join(render_import_fragment(r, r['id'] == at) for r in fragments)
        return self.fragment_page(content)

    def original(self, db, row):
        """The original file, byte for byte as imported (stored in the base)."""
        data = db.execute('SELECT original FROM imports WHERE source_id=? AND version=?',
                          (row['source_id'], row['version'])).fetchone()['original']
        if not data:
            return self.page('<p>Исходный файл не сохранён.</p>' + BACK, 404)
        name = (row['filename'] or 'document').replace('\\', '/').rsplit('/', 1)[-1]
        kind, disposition, _ = ORIGINAL_TYPES.get(os.path.splitext(name)[1].lower(), ('application/octet-stream', 'attachment', ''))
        fallback = re.sub(r'[^A-Za-z0-9._-]', '_', name)
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Disposition', f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}")
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'private, max-age=3600')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(bytes(data))

    def importing(self):
        """While the documents are being imported: a page that says so and refreshes itself."""
        content = ('<meta http-equiv="refresh" content="10"><h2>Идёт загрузка документов</h2>'
                   '<p>Навигатор импортирует и распознаёт документы; это занимает несколько минут. '
                   'Страница обновится сама.</p>')
        return self.page(content, 503, headers=(('Retry-After', '10'),))

    def do_GET(self):
        if IMPORTING['active']:
            return self.importing()
        parsed = urlparse(self.path)
        if parsed.path == '/':
            if history.enabled():
                return self.chat_page()
            return self.page(self.form())
        if parsed.path == '/documents':
            try:
                with closing(connect_existing(DB)) as db, db:
                    docs = imported_documents(db)
            except (sqlite3.Error, OSError):
                docs = []
            return self.page(BACK + render_documents(docs), kind='docs', title='Документы в базе')
        if parsed.path in ('/document', '/original'):
            query = parse_qs(parsed.query)
            source_id = query.get('source', [''])[0]
            version = query.get('version', [''])[0]
            try:
                with closing(connect_existing(DB)) as db, db:
                    if not has_imports(db):
                        return self.page('<p>Документ не найден.</p>' + BACK, 404)
                    if version:
                        row = db.execute('SELECT source_id, version, filename FROM imports WHERE source_id=? AND version=?',
                                         (source_id, version)).fetchone()
                    else:     # the latest imported version
                        row = db.execute('SELECT source_id, version, filename FROM imports WHERE source_id=? '
                                         'ORDER BY rowid DESC LIMIT 1', (source_id,)).fetchone()
                    if row is None:
                        return self.page('<p>Документ не найден.</p>' + BACK, 404)
                    if parsed.path == '/original':
                        return self.original(db, row)
                    return self.document_page(db, row, query.get('at', [''])[0])
            except (sqlite3.Error, OSError):
                return self.page('<p>Технический сбой при открытии документа. Повторите попытку.</p>' + BACK, 503)
        if parsed.path != '/fragment':
            return self.page('<p>Страница не найдена.</p>' + BACK, 404)
        fragment_id = parse_qs(parsed.query).get('id', [''])[0]
        cited_range = None
        at = re.fullmatch(r'(\d{1,7})-(\d{1,7})', parse_qs(parsed.query).get('at', [''])[0])
        if at:
            cited_range = (int(at.group(1)), int(at.group(2)))
        try:
            with closing(connect_existing(DB)) as db, db:
                tables = {r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'import_fragments' in tables:
                    imported_row = db.execute('SELECT f.*, d.filename AS title FROM import_fragments f JOIN imports d ON d.source_id=f.source_id AND d.version=f.version WHERE f.id=?', (fragment_id,)).fetchone()
                    if imported_row is not None:
                        from .retrieval import context as import_context
                        linked = import_context(db, fragment_id)
                        # Neighbours belong to exactly the same imported version;
                        # linked footnotes remain available after the excerpt.
                        rowid = db.execute('SELECT rowid FROM import_fragments WHERE id=?', (fragment_id,)).fetchone()[0]
                        import json
                        neighbours = [imported_row]
                        if json.loads(imported_row['metadata']).get('type') == 'paragraph':
                            before = db.execute("SELECT * FROM import_fragments WHERE source_id=? AND version=? AND rowid < ? AND locator LIKE 'body/%/paragraph' ORDER BY rowid DESC LIMIT 1", (imported_row['source_id'], imported_row['version'], rowid)).fetchall()
                            after = db.execute("SELECT * FROM import_fragments WHERE source_id=? AND version=? AND rowid > ? AND locator LIKE 'body/%/paragraph' ORDER BY rowid LIMIT 1", (imported_row['source_id'], imported_row['version'], rowid)).fetchall()
                            neighbours = list(reversed(before)) + neighbours + after
                        context = [dict(r) for r in neighbours]
                        seen = {r['id'] for r in context}
                        context.extend(r for r in linked if r['id'] not in seen)
                        content = source_heading(db, imported_row, cited_range)
                        content += ''.join(render_import_fragment(r, r['id'] == fragment_id, cited_range) for r in context)
                        return self.fragment_page(content)
                try:
                    ev = db.execute('SELECT f.*, d.title FROM evidence_fragments f JOIN evidence_documents d ON d.id=f.document_id WHERE f.id=?', (fragment_id,)).fetchone()
                except sqlite3.OperationalError:
                    ev = None
                if ev:
                    ctx = db.execute('SELECT * FROM evidence_fragments WHERE document_id=? ORDER BY rowid', (ev['document_id'],)).fetchall()
                    return self.fragment_page('<h2>' + esc(ev['title']) + '</h2>' + ''.join('<p' + (' id="selected"' if x['id'] == fragment_id else '') + '><strong>' + esc(x['locator']) + '</strong> ' + esc(x['text']) + '</p>' for x in ctx))
                row = db.execute('SELECT f.*, d.title FROM fragments f JOIN documents d ON d.id=f.document_id AND d.version=f.version WHERE f.id=?', (fragment_id,)).fetchone()
                if row is None:
                    try:
                        row = db.execute('SELECT f.*, d.filename AS title FROM import_fragments f JOIN imports d ON d.source_id=f.source_id AND d.version=f.version WHERE f.id=?', (fragment_id,)).fetchone()
                    except sqlite3.OperationalError:
                        row = None
                    if row is None:
                        return self.page('<p>Фрагмент не найден.</p>' + BACK, 404)
                    context = db.execute('SELECT * FROM import_fragments WHERE source_id=? AND version=? ORDER BY rowid', (row['source_id'], row['version'])).fetchall()
                    content = source_heading(db, row, cited_range)
                    content += ''.join(render_import_fragment(r, r['id'] == fragment_id, cited_range) for r in context)
                    return self.fragment_page(content)
                context = db.execute('SELECT * FROM fragments WHERE document_id=? AND version=? ORDER BY id', (row['document_id'], row['version'])).fetchall()
                content = '<h2>' + esc(row['title']) + '</h2><p class="ver">Версия ' + esc(row['version']) + '; выбран ' + esc(row['locator']) + ' (' + esc(row['id']) + ')</p>'
                content += ''.join('<p' + (' id="selected"' if r['id'] == fragment_id else '') + '><strong>' + esc(r['locator']) + '</strong> ' + esc(r['text']) + '</p>' for r in context)
                return self.fragment_page(content)
        except (sqlite3.Error, OSError):
            return self.page('<p>Технический сбой при открытии источника. Повторите попытку.</p>' + BACK, 503)

    def do_POST(self):
        if IMPORTING['active']:
            return self.importing()
        if self.path in ('/new', '/clear'):
            conversation = history.from_cookie(self.headers.get('Cookie'))
            if self.path == '/clear' and conversation:
                with closing(connect(DB)) as db, db:
                    history.clear(db, conversation)
                return self.redirect('/')
            return self.redirect('/', (self.cookie(history.new_id()),))
        if self.path != '/ask':
            return self.page('<p>Страница не найдена.</p>' + BACK, 404)
        try:
            length = int(self.headers.get('Content-Length', '0') or 0)
        except ValueError:
            length = -1
        if length < 0 or length > 10000:
            return self.page('<p>Слишком длинный запрос.</p>' + BACK, 400)
        fields = parse_qs(self.rfile.read(length).decode('utf-8', 'replace'), keep_blank_values=True)
        question = fields.get('question', [''])[0]
        conversation = history.from_cookie(self.headers.get('Cookie')) if history.enabled() else None
        # A browser in a dialogue (cookie) gets the dialogue; a request without the cookie
        # (scripts, the evaluator) gets the single answer page, as before.
        if conversation and question.strip() and len(question) <= 2000:
            return self.chat_post(conversation, fields)
        return self.answer_post(fields)

    def answer_post(self, fields):
        try:
            question = fields.get('question', [''])[0]
            if len(question) > 2000:
                return self.page('<p>Слишком длинный вопрос.</p>' + BACK, 400)
            if not question.strip():
                return self.page(self.form(question) + '<p>Введите вопрос.</p>', 400)
            with closing(connect_existing(DB)) as db, db:
                tables = {r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                from . import rag
                if rag.enabled() and 'import_fragments' in tables:
                    # RAG pipeline (stage R5); None → the extractive pipeline below.
                    rag_when = fields.get('date', [''])[0] or None
                    if rag_when:
                        from .evidence import iso
                        iso(rag_when)
                    failure = []
                    rag_result = rag.answer(db, question, rag_when, failure=failure)
                    if rag_result is not None:
                        return self.page(self.form(question) + render_rag(db, rag_result, rag_when, question))
                    # No generated answer: the page says so plainly before the fragments found.
                    self.notice = (rag.FAILURES.get(failure[0] if failure else 'unavailable', rag.FAILURES['unavailable'])
                                   + ' Ниже — найденные фрагменты документов без обработки моделью: проверьте их сами.')
                if 'evidence_topics' in tables and 'import_fragments' in tables:
                    from .evidence import query as evidence_query, keyword_matches
                    from .questions import split_question
                    from .procedures import staged
                    parts = [question] if staged(question) else split_question(question)
                    checked = [(p, evidence_query(db, p, fields.get('date',[''])[0] or None, {'категория': fields.get('category',[''])[0]}, imported_only=True)) for p in parts]
                    import json
                    linked_topics = db.execute('''SELECT DISTINCT t.keywords, i.id, i.metadata FROM evidence_topics t
                        JOIN evidence_rules r ON r.topic=t.id
                        JOIN import_fragments i ON i.id=r.fragment_id''').fetchall()
                    matched_link = any(any(keyword_matches(k, part) for k in json.loads(row['keywords']))
                                       for row in linked_topics for part in parts)
                    if matched_link or any(result is not None for _, result in checked):
                        content = self.form(question) + '<h2>Результат проверки</h2>'
                        some_answer = any(result and result['items'] for _, result in checked)
                        incomplete = any(result is None or not result['items'] for _, result in checked)
                        summary = ('Частичный ответ; недостающие части указаны ниже.' if some_answer and incomplete else
                                   'Найдены сведения; условия и ограничения указаны ниже.' if some_answer else
                                   'Подтверждённый ответ не найден; пояснения приведены ниже.')
                        content += '<h3>Краткий результат: ' + esc(summary) + '</h3>'
                        content += '<h4>Условия: дата ' + esc(fields.get('date',[''])[0] or 'не указана') + '; категория ' + esc(fields.get('category',[''])[0] or 'не указана') + '</h4>'
                        def source_link(fid, item=None):
                            return cite(fid, item) if fid else ''
                        for part, checked_result in checked:
                            content += '<h3>' + esc(part) + '</h3>'
                            for linked in linked_topics:
                                if any(keyword_matches(k, part) for k in json.loads(linked['keywords'])):
                                    for note in import_notes(json.loads(linked['metadata'])):
                                        content += '<p>Ограничение: ' + esc(note) + source_link(linked['id']) + '</p>'
                            if checked_result is None:
                                content += '<p>Недостаточно подтверждённых сведений для этой части вопроса: ' + esc(part) + '.</p>'
                                continue
                            if checked_result['items']:
                                if staged(part):
                                    from .procedures import missing as missing_facets, expand_items
                                    expanded = expand_items(db, checked_result['items'])
                                    content += render_stages(expanded, 'Другие подтверждённые сведения', source_link)
                                    for gap in missing_facets(expanded, part):
                                        content += '<p>Недостаточно данных для части: ' + esc(gap) + '.</p>'
                                else:
                                    content += '<h4>Подтверждённые пункты; порядок выполнения уточните по источнику</h4><ol>'
                                    for item in checked_result['items']:
                                        content += '<li>' + esc(item['text']) + source_link(item['basis']) + '</li>'
                                    content += '</ol>'
                            for item in checked_result['limits']:
                                content += '<p>Ограничение: ' + esc(item['text']) + source_link(item['basis']) + '</p>'
                            for item in checked_result['questions']:
                                content += '<p>' + esc(item) + '</p>'
                            for item in checked_result['conflicts']:
                                content += '<p>Обнаружено противоречие оснований; применимый вариант не установлен: ' + esc(', '.join(item['rules'])) + source_link(item['basis']) + '</p>'
                        return self.page(content)
                try:
                    from .evidence import query
                    result = query(db, question, fields.get('date',[''])[0] or None, {'категория': fields.get('category',[''])[0]})
                except sqlite3.OperationalError as exc:
                    if 'no such table: evidence_topics' not in str(exc): raise
                    result = None
                if result is None:
                    generator = None
                    if os.environ.get('NAVIGATOR_MODEL'):
                        from .local_model import answer as local_answer
                        generator = lambda rows: local_answer(question, rows, os.environ['NAVIGATOR_MODEL'])
                    when = fields.get('date', [''])[0] or None
                    if when:
                        from .evidence import iso
                        iso(when)  # malformed date → ValueError → handled as a bad request below
                    status, answer = form_answer(db, question, generator, when)
                    version_notes = []
                    has_imports = bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='import_fragments'").fetchone())
                    # Notes on excluded versions also explain an "insufficient" result.
                    staged_rows, missing_parts, _ = imported_parts(db, question, when, version_notes) if has_imports and status in ('imported', 'imported_partial', 'insufficient_procedure', 'insufficient') else ([], [], False)
                    if status == 'support':
                        from .procedures import source_limits
                        cited = []
                        for source in answer['sources']:
                            for units in source['facets'].values():
                                cited.extend(dict(u, source_id=source['source_id'], version=source['version']) for u in units)
                        source_notes = source_limits(db, cited)
                        import json
                        import_meta = {}
                        for unit in cited + [i for e in answer['extra'] for i in e['items']]:
                            r = db.execute('SELECT metadata FROM import_fragments WHERE id=?', (unit['id'],)).fetchone()
                            if r: import_meta[unit['id']] = json.loads(r['metadata'])
                    else:
                        import_meta = {}  # (was reset unconditionally, dropping OCR notes of support citations)
                    cited_labels = []
                    if status in ('imported', 'imported_partial'):
                        import json
                        for _, fid in answer:
                            r = db.execute('SELECT metadata FROM import_fragments WHERE id=?', (fid,)).fetchone()
                            if r: import_meta[fid] = json.loads(r['metadata'])
                        # Limitations stated by the cited versions themselves.
                        from .procedures import source_limits
                        cited = []
                        for text, fid in answer:
                            r = db.execute('SELECT source_id, version FROM import_fragments WHERE id=?', (fid,)).fetchone()
                            if r: cited.append({'id': fid, 'text': text, 'source_id': r['source_id'], 'version': r['version']})
                        source_notes = source_limits(db, cited)
                        # Requisites of the cited versions as stated in their texts.
                        from .requisites import requisites as doc_requisites, label as requisites_label
                        cited_labels = []
                        for key in dict.fromkeys((c['source_id'], c['version']) for c in cited):
                            name = db.execute('SELECT filename FROM imports WHERE source_id=? AND version=?', key).fetchone()
                            cited_labels.append((name['filename'] if name else '', requisites_label(doc_requisites(db, *key))))
                else:
                    content = self.form(question) + '<h2>Подтверждённые сведения</h2>'
                    def link(fid):
                        return ' <a href="/fragment?id=' + quote(fid, safe='') + '">' + esc(fid) + '</a>' if fid else ''
                    for item in result['items']:
                        content += '<p>' + esc(item['text']) + link(item['basis']) + '</p>'
                    for item in result['limits']:
                        content += '<p>Ограничение: ' + esc(item['text']) + link(item['basis']) + '</p>'
                    for item in result['questions']:
                        content += '<p>' + esc(item) + '</p>'
                    for item in result['conflicts']:
                        content += '<p>Обнаружено противоречие оснований; применимый вариант не установлен: ' + esc(', '.join(item['rules'])) + link(item['basis']) + '</p>'
                    return self.page(content)
            content = self.form(question)
            def version_block():
                return ''.join('<p>Ограничение: ' + esc(note) + (' ' + cite(fid) if fid else '') + '</p>'
                               for note, fid in version_notes)
            if status == 'insufficient':
                content += '<p>Недостаточно данных для ответа в доступных материалах.</p>' + version_block()
            elif status == 'insufficient_procedure':
                content += '<p>Недостаточно данных для частей вопроса: ' + esc('; '.join(missing_parts)) + '.</p>'
            elif status == 'insufficient_context':
                content += '<p>Недостаточно контекста для вывода: фрагмент слишком длинный для безопасного показа целиком.</p>'
            elif status == 'support':
                content += render_support(answer, import_meta, source_notes)
            elif status in ('imported', 'imported_partial'):
                if status == 'imported_partial':
                    content += '<p>Недостаточно данных для части вопроса: ' + esc('; '.join(missing_parts)) + '.</p>'
                content += '<h2>Найденные фрагменты</h2>'
                if cited_labels and all(text for _, text in cited_labels):
                    content += ('<p>Применимость и полнота проверены только по реквизитам и ссылкам в тексте импортированных '
                                'документов' + (' на ' + esc(when) if when else ' на текущую дату') + '; сверьте источник.</p>')
                else:
                    content += '<p>Применимость и полнота не проверены; дата вступления и связь редакций не подтверждены. Выбранная дата не определяет применимую редакцию. Сверьте источник.</p>'
                for name, text in cited_labels:
                    if text:
                        content += '<p>Источник: ' + esc((name + ' — ') if name else '') + esc(text) + ' (по тексту документа).</p>'
                content += version_block()
                from .procedures import staged, stage, CONDITION
                def import_link(fragment_id, item=None):
                    return ''.join(' <em>' + esc(note) + '</em>' for note in import_notes(import_meta.get(fragment_id, {}))) + cite(fragment_id, item)
                if staged(question):
                    unit_of = {(r['text'], r['id']): r for r in staged_rows if isinstance(r, dict)}
                    items = [{'text': text, 'basis': fid, 'stage': unit_of.get((text, fid), {}).get('stage', stage(text)),
                              'condition': bool(CONDITION.search(text)),
                              'start': unit_of.get((text, fid), {}).get('start'), 'end': unit_of.get((text, fid), {}).get('end')}
                             for text, fid in answer]
                    content += render_stages(items, 'Другие сведения', import_link)
                else:
                    content += '<ol>'
                    for text, fragment_id in answer:
                        content += '<li>' + esc(text) + import_link(fragment_id) + '</li>'
                    content += '</ol>'
                for note, fid in source_notes:
                    content += '<p>Ограничение: ' + esc(note) + ' <a href="/fragment?id=' + quote(fid, safe='') + '">' + esc(fid) + '</a></p>'
            else:
                content += '<h2>Ответ и порядок действий</h2><p>Только вымышленный учебный пример.</p>' + ('<p>Частичный ответ: проверьте приведённые основания.</p>' if os.environ.get('NAVIGATOR_MODEL') else '') + '<ol>'
                for text, fragment_id in answer:
                    content += '<li>' + esc(text) + ' <a href="/fragment?id=' + quote(fragment_id, safe='') + '">' + esc(fragment_id) + '</a></li>'
                content += '</ol>'
            return self.page(content)
        except (sqlite3.Error, OSError, UnicodeError, ValueError):
            return self.page(self.form() + '<p>Технический сбой. Повторите попытку.</p>', 503)
        except Exception as exc:
            from .local_model import ModelError
            if not isinstance(exc, ModelError): raise
            return self.page(self.form(question) + '<p>Ошибка локальной модели или недопустимый результат. Повторите попытку.</p>', 503)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', nargs='?', choices=['serve', 'import-fixture', 'import', 'audit', 'load-evidence', 'embed'], default='serve')
    parser.add_argument('paths', nargs='*')
    parser.add_argument('--manifest', default='sdd/sources/manifest.json')
    parser.add_argument('--report', help='Local JSON report path')
    args = parser.parse_args()
    if args.command == 'import-fixture':
        import_fixture(DB)
        print('Synthetic fixture imported')
    elif args.command == 'load-evidence':
        import json
        from pathlib import Path
        from .evidence import load
        if len(args.paths) != 1: parser.error('load-evidence requires one JSON file')
        Path(DB).parent.mkdir(parents=True, exist_ok=True)
        from .importer import init
        with closing(connect(DB)) as db, db:
            init(db)
            load(db, json.loads(Path(args.paths[0]).read_text(encoding='utf-8')))
        print('Evidence catalogue loaded')
    elif args.command == 'embed':
        # Fill the local vector cache for dense/hybrid retrieval; texts stay local.
        from . import dense
        enc = dense.encoder()
        if enc is None:
            raise SystemExit('Dense model unavailable: ' + dense.status().get('fallback', 'set NAVIGATOR_EMBED_MODEL'))
        with closing(connect(DB)) as db:
            print(f'Embedded fragments: {dense.ensure_vectors(db, enc)} (model {enc.name})')
    elif args.command in ('import', 'audit'):
        import json
        from .importer import batch, audit
        if args.command == 'import' and not args.paths:
            parser.error('import requires files or ZIP archives')
        result = batch(DB, args.paths, args.manifest) if args.command == 'import' else audit(DB, args.manifest)
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        if args.report:
            from pathlib import Path
            report = Path(args.report)
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(rendered + '\n', encoding='utf-8')
            print('Report saved locally')
        else:
            print(rendered)
        if args.command == 'import' and result.get('error', 0):
            raise SystemExit(1)
    else:
        # NAVIGATOR_IMPORT_DIR: import the documents of that folder (the container sets
        # /data/originals). The web server is up at once; while the import runs (OCR of
        # scans takes minutes) every page says so instead of the site being unreachable.
        import threading
        from .autoimport import from_environment
        def log(message):
            IMPORTING['last'] = message
            print(message, flush=True)
        def run_import():
            try:
                from_environment(DB, log=log)
            finally:
                IMPORTING['active'] = False
        if os.environ.get('NAVIGATOR_IMPORT_DIR'):
            IMPORTING['active'] = True
            threading.Thread(target=run_import, daemon=True).start()
        # One thread per request: a generated answer takes seconds and must not block other visitors.
        server = ThreadingHTTPServer((os.environ.get('HOST', '127.0.0.1'), int(os.environ.get('PORT', '8000'))), Handler)
        server.daemon_threads = True
        server.serve_forever()
