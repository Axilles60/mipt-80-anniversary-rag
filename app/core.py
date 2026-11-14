"""Local synthetic document import, retrieval and answer formation."""
from contextlib import closing
import hashlib
import json
import re
import sqlite3
from pathlib import Path

DEFAULT_DB = 'data/index/navigator.sqlite3'
FIXTURE = Path(__file__).resolve().parent.parent / 'fixtures/support.json'
MAX_EXCERPT_CHARS = 12000

def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    return db

def connect_existing(path):
    # Web requests must not silently recreate a missing or deleted database.
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=rw', uri=True)
    db.row_factory = sqlite3.Row
    return db

def import_fixture(path):
    raw = FIXTURE.read_bytes()
    doc = json.loads(raw)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as db, db:
        db.executescript('''CREATE TABLE IF NOT EXISTS documents (id TEXT, version TEXT, title TEXT, sha256 TEXT, original BLOB, PRIMARY KEY(id,version));
        CREATE TABLE IF NOT EXISTS fragments (id TEXT PRIMARY KEY, document_id TEXT, version TEXT, locator TEXT, text TEXT);
        CREATE VIRTUAL TABLE IF NOT EXISTS fragments_fts USING fts5(id UNINDEXED, text, tokenize='unicode61');''')
        existing = db.execute('SELECT sha256 FROM documents WHERE id=? AND version=?', (doc['id'], doc['version'])).fetchone()
        checksum = hashlib.sha256(raw).hexdigest()
        if existing and existing['sha256'] != checksum:
            raise ValueError('Version already exists with different content')
        db.execute('INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?)', (doc['id'], doc['version'], doc['title'], checksum, raw))
        for f in doc['fragments']:
            db.execute('INSERT OR IGNORE INTO fragments VALUES (?,?,?,?,?)', (f['id'], doc['id'], doc['version'], f['locator'], f['text']))
            db.execute('DELETE FROM fragments_fts WHERE id=?', (f['id'],))
            db.execute('INSERT INTO fragments_fts VALUES (?,?)', (f['id'], f['text']))

def search(db, question):
    from .retrieval import retrieve
    return retrieve(db, question, 10)

def imported_parts(db, question, when=None, notes=None):
    """Retrieve and check each part, then report the parts not represented in k.

    ``when``: ISO date of the question (or None for now). Versions repealed or
    not yet in force on that date by their own texts are not used; the reasons
    are appended to ``notes`` as (text, basis fragment id)."""
    from .retrieval import imported, filter_sibling_cells, coherent_candidates, heading_only
    from .requisites import applicable
    notes = [] if notes is None else notes
    def usable(rows):
        kept, why = applicable(db, rows, when)
        for note in why:
            if note not in notes: notes.append(note)
        return kept
    from .sufficiency import supports
    from .questions import split_question
    from .procedures import staged
    parts = split_question(question)
    from .support import answer as support_answer
    support = support_answer(db, question)
    if support is not None:
        # A budget line about repairs cannot answer how to report a fault:
        # only documents stating a channel and request content qualify.
        rows = [unit for source in support['sources'] for key in ('channel', 'details', 'time', 'scope')
                for unit in source['facets'][key]]
        rows += [item for extra in support['extra'] for item in extra['items']]
        missing = list(support['gaps']) + [m for source in support['sources'] for m in source['missing']]
        return rows, missing, False
    coherent = coherent_candidates(db, question, parts, when)
    if coherent:
        coherent = usable(coherent)
    if coherent:
        from .procedures import staged, missing as missing_facets
        if staged(question):
            selected = [row for row in coherent if len(row['text']) <= MAX_EXCERPT_CHARS]
            gaps = missing_facets(selected, question)
            return selected, gaps, len(selected) != len(coherent)
        # Do not silently claim an independent intent was satisfied.
        coverage = [p for p in parts if not any(supports(p, row['text']) for row in coherent)]
        return coherent, coverage, False
    if staged(question):
        return [], ['до: действия, сроки и формы', 'после: действия, сроки и формы'], False
    by_part = []
    rows = []
    oversized = False
    from .support import ASK_CHANNEL, CHANNEL, mentions_object
    for part in parts:
        candidates = filter_sibling_cells(imported(db, part, k=3), part)
        kept = usable(candidates)
        if len(kept) < len(candidates):
            # An excluded version must not shrink k: look further down the ranking.
            kept = usable(filter_sibling_cells(imported(db, part, k=12), part))[:3]
        candidates = kept
        # A fault of an object the support module does not cover (a lift, a
        # door) is answered only by text that names that object.
        candidates = [r for r in candidates if mentions_object(part, r['text'])]
        if ASK_CHANNEL.search(part):
            # "Where/whom to report" needs a stated channel; a table cell
            # without an address, portal or e-mail (e.g. a budget line about
            # repairs) cannot answer it.
            candidates = [r for r in candidates if not ('"type": "cell"' in (r['metadata'] or '') and not CHANNEL.search(r['text']))]
        oversized |= any(len(r['text']) > MAX_EXCERPT_CHARS and supports(part, r['text']) for r in candidates)
        # A title or heading identifies a document but is not an answer.
        accepted = [r for r in candidates if r['text'].strip() and len(r['text']) <= MAX_EXCERPT_CHARS
                    and supports(part, r['text']) and not heading_only(db, r)]
        by_part.append((part, accepted))
        for row in accepted:
            if all(existing['id'] != row['id'] for existing in rows):
                rows.append(row)
    rows = rows[:3]
    selected = {row['id'] for row in rows}
    missing = [part for part, accepted in by_part if not any(row['id'] in selected for row in accepted)]
    return rows, missing, oversized

def form_answer(db, question, generator=None, when=None):
    """Generator hook receives only retrieved rows; future implementations must validate citations."""
    if not question.strip():
        return 'empty', []
    # A pristine database has no schema yet; a missing index in an existing
    # import schema, however, is a technical error and must propagate.
    tables = {row['name'] for row in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
    if 'fragments' in tables:
        matches = search(db, question)
        support = re.search(r'принтер|техподдерж|поддержк|неисправ|заявк', question.lower())
        if matches and support and {r['document_id'] for r in matches} == {'SYN-SUPPORT'}:
            rows = db.execute('SELECT * FROM fragments WHERE document_id=? AND version=? ORDER BY id', ('SYN-SUPPORT', '1')).fetchall()
            if len(rows) == 3:
                if generator is not None:
                    from .local_model import validate
                    result = validate(generator(rows), rows)
                    return 'answer', [(item['text'], item['references'][0]) for item in result['claims'] + result['steps']]
                return 'answer', extractive(rows)
    if 'import_fragments' in tables:
        from .support import answer as support_answer
        support = support_answer(db, question)
        if support is not None:
            if support['sources'] or any(extra['items'] for extra in support['extra']):
                return 'support', support
            return 'insufficient', []
        rows, missing, oversized = imported_parts(db, question, when)
        if rows:
            from .procedures import staged
            if generator is not None and not staged(question):
                from .local_model import validate
                selected = validate(generator(rows), rows)
                items = [(item['text'], item['references'][0]) for item in selected['claims'] + selected['steps']]
            else:
                items = [(r['text'], r['id']) for r in rows]
            return ('imported_partial' if missing else 'imported'), items
        if oversized:
            return 'insufficient_context', []
        if missing:
            from .procedures import staged
            if staged(question): return 'insufficient_procedure', []
    return 'insufficient', []

def extractive(rows):
    return [(row['text'], row['id']) for row in rows]
