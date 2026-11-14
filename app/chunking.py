"""Hierarchical chunks of an imported version (sdd/rag-plan.md, stage R1).

Strategy, by structure and file type (cf. sdd/rag-quality.md, item 1):
* structural — headings (DOCX title/heading style, numbered section lines,
  an unstyled title line) open a SECTION, the parent of the chunks inside it;
  a PDF without headings uses the PAGE as the parent;
* recursive — a PDF/OCR page is cut into line blocks (numbered items,
  paragraphs); a long block into sentences;
* fixed — a sentence longer than MAX_TOKENS is cut by tokens, with OVERLAP;
* by file type — a DOCX table row and an XLSX sheet row become one chunk
  "column header: value; …" (XLSX rows carry the path of their parent rows
  when the sheet has a level column); footnotes and forms are typed.

Every child chunk has two texts. Its ADDRESS is the exact character range of
its own block inside the imported fragment — what a citation opens and
highlights. Its INDEX TEXT (BM25 and embeddings) is the document title, the
section path, an overlap of up to OVERLAP_TOKENS from the previous block of
the section, and the block, cleaned of line-break hyphenation. Tokens are
counted with the embedding model's tokenizer when it is installed, so no
chunk is silently truncated at 512 tokens.

Chunks are built at import; older databases are chunked lazily (ensure()).
A new version is chunked without touching others.
"""
from functools import lru_cache
import json
import os
import re

from .procedures import _HEADING, _split
from .stemming import definitions, stem_text

MAX_TOKENS = 300       # child chunk (E5 limit is 512 with the prefix and title)
OVERLAP_TOKENS = 50
PARENT_TOKENS = 900    # parent text given to the generator is capped
SCHEMA = '''
CREATE TABLE IF NOT EXISTS chunks (id TEXT PRIMARY KEY, source_id TEXT, version TEXT, fragment_id TEXT,
    start INTEGER, end INTEGER, level INTEGER, parent_id TEXT, ord REAL, text TEXT, index_text TEXT, meta TEXT);
CREATE INDEX IF NOT EXISTS chunks_version ON chunks(source_id, version);
CREATE INDEX IF NOT EXISTS chunks_parent ON chunks(parent_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(id UNINDEXED, index_text);
CREATE TABLE IF NOT EXISTS abbreviations (abbr TEXT, expansion TEXT, source_id TEXT, version TEXT, chunk_id TEXT);
CREATE TABLE IF NOT EXISTS chunk_index (key TEXT PRIMARY KEY, value TEXT);
'''
# Changing how chunks or their full-text index are built bumps this value;
# ensure() then rebuilds every version once.
INDEX_VERSION = '4-table-headers'
_TITLE_STYLES = ('title', 'heading', 'заголов', 'subtitle')
# A template: blanks to fill in, or a capitalised FORM title (not "по форме КМ-7").
_FORM = re.compile(r'_{4,}|«\s*_+\s*»|\bФОРМА\b')
_TOC = re.compile(r'(?:\.{4,}|…{2,})\s*\d+\s*$')
_PAGE_NUMBER = re.compile(r'^\s*(?:стр(?:аница)?\.?\s*)?\d{1,4}\s*(?:из\s+\d+)?\s*$', re.I)
_ITEM = re.compile(r'^\s*((?:\d{1,2}\.)+\d{0,3}|\d{1,2}\))\s')


@lru_cache(maxsize=1)
def _tokenizer():
    """The embedding model's tokenizer, or None (then ~3.5 characters per token)."""
    try:
        from tokenizers import Tokenizer
        from .dense import model_path
        path = model_path() / 'tokenizer.json'
        return Tokenizer.from_file(str(path)) if path.is_file() else None
    except Exception:
        return None


def tokens(text):
    # A text no longer than OVERLAP_TOKENS characters cannot exceed any budget
    # here (a token covers at least one character): skip loading the tokenizer.
    if len(text) <= OVERLAP_TOKENS:
        return max(1, round(len(text) / 3.5))
    tok = _tokenizer()
    if tok is not None:
        return len(tok.encode(text, add_special_tokens=False).ids)
    return max(1, round(len(text) / 3.5))


def clean(text):
    """Index text: join line-break hyphenation and whitespace (the address keeps the raw text)."""
    text = re.sub(r'(\w)[-­]\s*\n\s*(\w)', r'\1\2', text)
    return re.sub(r'\s+', ' ', text).strip()


def _sentence_spans(text, start, end):
    out, pos = [], start
    for m in re.finditer(r'(?<=[.!?;])\s+(?=[А-ЯЁA-Z«\d])', text[start:end]):
        cut = start + m.start()
        if cut > pos: out.append((pos, cut))
        pos = start + m.end()
    if pos < end: out.append((pos, end))
    return out


def _fixed(text, start, end):
    """Windows of at most MAX_TOKENS with OVERLAP_TOKENS, cut at word boundaries."""
    words = [(m.start() + start, m.end() + start) for m in re.finditer(r'\S+', text[start:end])]
    out, i = [], 0
    while i < len(words):
        j = i
        while j < len(words) and tokens(text[words[i][0]:words[j][1]]) <= MAX_TOKENS: j += 1
        j = max(j, i + 1)
        out.append((words[i][0], words[j - 1][1]))
        if j >= len(words): break
        back = j
        while back > i + 1 and tokens(text[words[back - 1][0]:words[j - 1][1]]) <= OVERLAP_TOKENS: back -= 1
        i = back if back > i else j
    return out


def _pieces(text, start, end):
    if tokens(text[start:end]) <= MAX_TOKENS:
        return [(start, end)]
    out, cur = [], None
    for s, e in _sentence_spans(text, start, end):
        if tokens(text[s:e]) > MAX_TOKENS:
            if cur: out.append(cur); cur = None
            out.extend(_fixed(text, s, e)); continue
        if cur and tokens(text[cur[0]:e]) <= MAX_TOKENS:
            cur = (cur[0], e)
        else:
            if cur: out.append(cur)
            cur = (s, e)
    if cur: out.append(cur)
    return out


def _tail(text, budget=OVERLAP_TOKENS):
    """Last words of a block within the overlap budget (one tokenizer call per trim)."""
    words = text[-budget * 6:].split()  # a generous character window first
    while words and tokens(' '.join(words)) > budget:
        words = words[max(1, len(words) // 8):]
    return ' '.join(words)


def _is_heading(text, meta, first):
    clean_text = re.sub(r'\s+', ' ', text).strip()
    if not clean_text: return False
    if str(meta.get('style') or '').lower().startswith(_TITLE_STYLES): return True
    if _HEADING.match(clean_text): return True
    return first and len(clean_text) <= 200 and not re.search(r'[.;:!?…]$', clean_text)


def _kind(text, meta):
    if meta.get('type') == 'footnote': return 'footnote'
    if _TOC.search(text): return 'toc'
    if _FORM.search(text): return 'form'
    return 'norm'


def _repeated_lines(rows):
    """Lines that repeat on at least half of the pages: running headers/footers."""
    pages = [r for r in rows if json.loads(r['metadata'] or '{}').get('type') in ('pdf_text', 'ocr')]
    if len(pages) < 3: return set()
    counts = {}
    for r in pages:
        for line in {re.sub(r'\s+', ' ', l).strip() for l in (r['text'] or '').split('\n')}:
            if line: counts[line] = counts.get(line, 0) + 1
    return {line for line, n in counts.items() if n >= max(3, len(pages) // 2)}


def build(db, source_id, version, title=None):
    """Chunks of one version: parents (level 1) then children (level 2), with metadata."""
    rows = db.execute('SELECT rowid AS rowid, * FROM import_fragments WHERE source_id=? AND version=? ORDER BY rowid',
                      (source_id, version)).fetchall()
    base = f'{source_id}@{version}'
    noise = _repeated_lines(rows)
    has_headings = False
    sections, children = [], []
    section = None
    def open_section(name, fragment_id, start, end, page=None):
        nonlocal section
        section = {'id': f'{base}#s{len(sections)}', 'title': name, 'fragment_id': fragment_id,
                   'start': start, 'end': end, 'page': page, 'path': [], 'previous': ''}
        section['path'] = [name] if name else []
        sections.append(section)
    open_section('', None, None, None)
    doc_title = title
    first = True
    tables, sheet_levels = {}, {}
    ord_ = 0
    for row in rows:
        meta = json.loads(row['metadata'] or '{}')
        kind = meta.get('type')
        text = row['text'] or ''
        page = meta.get('page')
        if kind == 'cell':
            continue  # sheet rows are assembled below
        if kind == 'table_row':
            prefix = row['locator'].rsplit('/row/', 1)[0]
            cells = [str(c) for c in meta.get('cells', [])]
            if prefix not in tables:
                tables[prefix] = cells; continue   # the first row names the columns
            header = tables[prefix]
            body = '; '.join((f'{h}: {c}' if h and h != c else c) for h, c in zip(header + [''] * len(cells), cells) if c.strip())
            ord_ += 1
            # The generator sees what the index sees: «column: value», not the bare cells.
            children.append({'fragment_id': row['id'], 'start': 0, 'end': len(text), 'text': body,
                             'index_body': body, 'section': section, 'ord': ord_,
                             'meta': {'type': 'table_row', 'kind': 'norm', 'page': page}})
            continue
        if kind in ('pdf_text', 'ocr') and not has_headings and section['title'] == '' and page and section.get('page') != page and children:
            open_section('', row['id'], 0, 0, page)   # page as parent while no heading structure is known
        for start, end in ([(s, e) for s, e, _ in _split(text, meta)] if kind in ('pdf_text', 'ocr')
                           else ([(text.find(text.strip()), text.find(text.strip()) + len(text.strip()))] if text.strip() else [])):
            block = text[start:end]
            line = re.sub(r'\s+', ' ', block).strip()
            if not line or line in noise or _PAGE_NUMBER.match(line):
                continue
            if _is_heading(block, meta, first):
                if first and doc_title is None: doc_title = line
                first = False
                has_headings = has_headings or bool(_HEADING.match(line) or str(meta.get('style') or '').lower().startswith(_TITLE_STYLES[1:3]))
                open_section(line, row['id'], start, end, page)
                continue
            if first and doc_title is None:
                # No separate title line: the first sentence names the document.
                doc_title = re.split(r'(?<=[.)])\s', line, 1)[0][:160]
            first = False
            for s, e in _pieces(text, start, end):
                piece = re.sub(r'\s+', ' ', text[s:e]).strip()
                if not piece: continue
                ord_ += 1
                number = _ITEM.match(piece)
                children.append({'fragment_id': row['id'], 'start': s, 'end': e, 'text': piece, 'index_body': clean(text[s:e]),
                                 'overlap': _tail(section['previous']), 'section': section, 'ord': ord_,
                                 'meta': {'type': kind or 'text', 'kind': _kind(piece, meta), 'page': page,
                                          'item': number.group(1).rstrip('.') if number else None,
                                          'ocr': kind == 'ocr'}})
                section['previous'] = piece
    # XLSX: one chunk per sheet row, with the path of parent rows if there is a level column.
    cells = db.execute('''SELECT id, metadata FROM import_fragments WHERE source_id=? AND version=?
                          AND json_extract(metadata, '$.type')='cell' ORDER BY rowid''', (source_id, version)).fetchall()
    sheets = {}
    for cell in cells:
        meta = json.loads(cell['metadata'])
        number = int(re.sub(r'^[A-Z]+', '', meta.get('range', '0')) or 0)
        value = meta.get('cached_value') if meta.get('formula') else meta.get('value')
        header = (meta.get('column_headers') or [{}])[-1].get('text', '')
        sheet = sheets.setdefault(meta.get('sheet'), {})
        sheet.setdefault(number, []).append((cell['id'], header, '' if value is None else str(value)))
    for sheet_name, sheet_rows in sheets.items():
        path = {}
        for number in sorted(sheet_rows):
            entries = sheet_rows[number]
            if all(h == v or not h for _, h, v in entries) and number == min(sheet_rows):
                continue  # header row
            pairs = [(h, v) for _, h, v in entries if v != '']
            level = next((int(float(v)) for h, v in pairs if re.match(r'уров|level', h, re.I) and re.fullmatch(r'\d+(?:\.0)?', v)), None)
            name = next((v for h, v in pairs if not re.fullmatch(r'[\d.,\s-]+', v) and not re.match(r'уров|level', h, re.I)), '')
            parents = [path[l] for l in sorted(path) if level is not None and l < level]
            if level is not None:
                path = {l: n for l, n in path.items() if l < level}; path[level] = name
            body = '; '.join(f'{h}: {v}' if h and h != v else v for h, v in pairs)
            ord_ += 1
            # Parent rows (directorate of a laboratory) are part of what the row means.
            children.append({'fragment_id': entries[0][0], 'start': None, 'end': None,
                             'text': (' › '.join(parents) + ' › ' if parents else '') + body,
                             'index_body': (' › '.join(parents) + ' › ' if parents else '') + body,
                             'section': sections[0], 'ord': ord_,
                             'meta': {'type': 'sheet_row', 'kind': 'norm', 'sheet': sheet_name, 'row': number,
                                      'parents': parents,
                                      # every non-empty cell: a citation of the row must show all of them
                                      'cells': [cid for cid, _, v in entries if v != '']}})
    doc_title = doc_title or ''
    out = []
    for p in sections:
        members = [c for c in children if c['section'] is p]
        if not members: continue
        body = '\n'.join(c['text'] for c in members)
        if tokens(body) > PARENT_TOKENS:
            body = body[:int(PARENT_TOKENS * 3.5)].rsplit(' ', 1)[0] + ' …'
        out.append({'id': p['id'], 'source_id': source_id, 'version': version,
                    'fragment_id': p['fragment_id'] or members[0]['fragment_id'], 'start': p['start'], 'end': p['end'],
                    'level': 1, 'parent_id': None, 'ord': members[0]['ord'] - 0.5,
                    'text': ((p['title'] + '\n') if p['title'] else '') + body, 'index_text': p['title'] or doc_title,
                    'meta': {'type': 'section', 'title': p['title'], 'page': p.get('page')}})
        for c in members:
            path = ' › '.join(x for x in [doc_title] + p['path'] if x)
            index = '\n'.join(x for x in (path, c.get('overlap', ''), c['index_body']) if x)
            out.append({'id': f"{base}#c{c['ord']}", 'source_id': source_id, 'version': version, 'fragment_id': c['fragment_id'],
                        'start': c['start'], 'end': c['end'], 'level': 2, 'parent_id': p['id'], 'ord': c['ord'],
                        'text': c['text'], 'index_text': index, 'meta': dict(c['meta'], section=p['title'], document=doc_title)})
    return out


def index_version(db, source_id, version):
    """(Re)build chunks and their full-text index for one version."""
    db.executescript(SCHEMA)
    chunks = build(db, source_id, version)
    with db:
        for (cid,) in db.execute('SELECT id FROM chunks WHERE source_id=? AND version=?', (source_id, version)).fetchall():
            db.execute('DELETE FROM chunks_fts WHERE id=?', (cid,))
        db.execute('DELETE FROM chunks WHERE source_id=? AND version=?', (source_id, version))
        db.execute('DELETE FROM abbreviations WHERE source_id=? AND version=?', (source_id, version))
        db.execute("INSERT OR IGNORE INTO chunk_index VALUES ('version', ?)", (INDEX_VERSION,))
        for c in chunks:
            db.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       (c['id'], c['source_id'], c['version'], c['fragment_id'], c['start'], c['end'], c['level'],
                        c['parent_id'], c['ord'], c['text'], c['index_text'], json.dumps(c['meta'], ensure_ascii=False)))
            if c['level'] == 2 and c['meta'].get('kind') != 'toc':
                # BM25 over Russian stems (stage R2), not raw word forms.
                db.execute('INSERT INTO chunks_fts VALUES (?,?)', (c['id'], stem_text(c['index_text'])))
                for abbr, expansion in definitions(c['text']):
                    db.execute('INSERT INTO abbreviations VALUES (?,?,?,?,?)', (abbr, expansion, source_id, version, c['id']))
    return len(chunks)


def ensure(db):
    """Chunk every imported version that has no chunks yet (older databases),
    and rebuild everything once when INDEX_VERSION changed."""
    db.executescript(SCHEMA)
    built = db.execute("SELECT value FROM chunk_index WHERE key='version'").fetchone()
    if built is None or built[0] != INDEX_VERSION:
        with db:
            db.execute('DELETE FROM chunks'); db.execute('DELETE FROM chunks_fts'); db.execute('DELETE FROM abbreviations')
            db.execute("INSERT OR REPLACE INTO chunk_index VALUES ('version', ?)", (INDEX_VERSION,))
    missing = db.execute('''SELECT source_id, version FROM imports i WHERE status != 'error' AND NOT EXISTS
        (SELECT 1 FROM chunks c WHERE c.source_id=i.source_id AND c.version=i.version)''').fetchall()
    for row in missing:
        index_version(db, row['source_id'], row['version'])
    return len(missing)
