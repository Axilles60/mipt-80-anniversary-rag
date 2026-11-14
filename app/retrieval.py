"""Local lexical retrieval with explicit, auditable synonym expansion."""
import re
import sqlite3

ALIASES = {
 'техподдерж': ['техническ','поддержк'], 'айти': ['техническ','поддержк'],
 'флс': ['финансов','лицев','счет'], 'смет': ['смет'],
 'поездк': ['командировк'], 'командировк': ['поездк'],
 'печата': ['принтер'], 'печат': ['принтер'], 'принтер': ['печат'],
 'сломал': ['ремонт','неисправ'], 'неработ': ['неисправ','ремонт'],
 'заявк': ['заявк'], 'сведен': ['содерж','заявк'], 'напис': ['содерж','заявк'],
 'кмо': ['концертн','зал'], 'цод': ['центр','обработк','данн'],
 'фэу': ['финансов','управлен'], 'кфо': ['финансов','обеспечен'],
 'урп': ['управлен','проект'],
}

def terms(question):
    normalized = re.sub(r'\bне\s+работ', 'неработ', question.lower().replace('ё','е'))
    words=re.findall(r'[а-яёa-z0-9]+', normalized)
    result=[]
    for word in words:
        if len(word)>=4: result.append(word[:6])
        for key, values in ALIASES.items():
            if word.startswith(key): result.extend(x[:6] for x in values)
    return list(dict.fromkeys(result))[:60]

def retrieve(db,question,k=3):
    if not 1 <= k <= 100: raise ValueError('invalid k')
    tokens=terms(question)
    if not tokens: return []
    query=' OR '.join('"'+x+'"*' for x in tokens)
    # Use SQLite parameter binding; every result remains a real, locatable fragment.
    return db.execute('SELECT f.* FROM fragments_fts x JOIN fragments f ON f.id=x.id WHERE fragments_fts MATCH ? ORDER BY bm25(fragments_fts),f.id LIMIT ?', (query,k)).fetchall()

def imported(db,question,k=3,gate=True):
    """Fragments for a question. ``gate`` (hybrid mode only) drops lexical
    candidates far in meaning from the question; it must be off when hits are
    aggregated to choose a document rather than used as answer items."""
    tokens=terms(question)
    if not tokens: return []
    q=' OR '.join('"'+x+'"*' for x in tokens)
    # Exact identifiers must be checked before applying k: unrelated years or
    # document numbers should not displace the requested source.
    identifiers = [re.sub(r'\s+', '', x).replace('–','-') for x in re.findall(r'(?<!\d)\d{1,6}\s*[-–/]\s*(?:19|20)\d{2}(?!\d)', question)]
    years = re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', question) if not identifiers else []
    candidates = db.execute('SELECT f.* FROM import_fts x JOIN import_fragments f ON f.id=x.id WHERE import_fts MATCH ? ORDER BY bm25(import_fts),f.id', (q,))
    from . import dense
    if dense.mode() != 'bm25':
        candidates = _with_dense(db, question, candidates.fetchall(), dense, gate)
    result=[]
    for row in candidates:
        text=row['text'].lower().replace('–','-')
        compact=re.sub(r'\s+', '', text)
        if identifiers and not all(re.search(r'(?<!\d)' + re.escape(identifier) + r'(?!\d)', compact) for identifier in identifiers): continue
        if years and not all(re.search(r'(?<!\d)'+year+r'(?!\d)', text) for year in years): continue
        result.append(row)
        if len(result)>=k: break
    return result

def _with_dense(db, question, lexical, dense, gate=True):
    """Dense-only or RRF-fused candidate order; BM25 order if the model is unavailable.

    Hybrid: the dense ranking reorders all lexical candidates and may add
    fragments without lexical overlap only if they clearly stand out (z)."""
    scored = dense.rank(db, question)
    if scored is None:
        return lexical
    threshold = dense.min_z()
    standout = [fid for fid, _, z in scored if z is not None and z >= threshold][:dense.DEPTH]
    if dense.mode() == 'dense':
        order = standout
    else:
        if gate:
            z = {fid: value for fid, _, value in scored}
            floor = dense.gate_z()
            # Shared words alone do not make a fragment relevant: drop lexical
            # candidates whose meaning is far from the question (z is None for
            # a corpus too small to standardise; then nothing is dropped).
            lexical = [r for r in lexical if z.get(r['id']) is None or z[r['id']] >= floor]
        lexical_ids = {r['id'] for r in lexical}
        added = set(standout)
        order = dense.fuse([r['id'] for r in lexical], [fid for fid, _, _ in scored if fid in lexical_ids or fid in added])
    rows = {r['id']: r for r in lexical}
    missing = [fid for fid in order if fid not in rows]
    for start in range(0, len(missing), 500):
        part = missing[start:start + 500]
        for row in db.execute('SELECT * FROM import_fragments WHERE id IN (%s)' % ','.join('?' * len(part)), part):
            rows[row['id']] = row
    return [rows[fid] for fid in order if fid in rows]

_TITLE_STYLES = ('title', 'heading', 'заголов', 'subtitle')

def heading_only(db, row):
    """True for a fragment that is only a document title or a section heading.

    Such a fragment can identify a document but states no rule, so it is not an
    answer item. Signals: a DOCX title/heading style; a numbered section heading
    line; or the first non-empty paragraph of a multi-paragraph document that
    is short and has no sentence-final punctuation (an unstyled title)."""
    import json
    from .procedures import _HEADING
    try:
        meta = json.loads(row['metadata'] or '{}')
    except (ValueError, TypeError):
        return False
    if meta.get('type') != 'paragraph':
        return False
    text = re.sub(r'\s+', ' ', row['text'] or '').strip()
    if not text:
        return False
    if str(meta.get('style') or '').lower().startswith(_TITLE_STYLES):
        return True
    if len(text) > 200 or re.search(r'[.;:!?…]$', text):
        return False
    if _HEADING.match(text):
        return True
    first = db.execute("SELECT id FROM import_fragments WHERE source_id=? AND version=? AND trim(text)<>'' "
                       "ORDER BY rowid LIMIT 2", (row['source_id'], row['version'])).fetchall()
    return len(first) == 2 and first[0]['id'] == row['id']

# These generic request words cannot by themselves identify a document's topic.
_GENERIC = ('каки', 'куда', 'когда', 'заявк', 'докуме', 'срок', 'сведен',
            'указат', 'содерж', 'нужн', 'обрат', 'сообщ', 'каков', 'после',
            'перед', 'описан', 'напис', 'поряд', 'форм', 'состав', 'дейст')

def focus_terms(question):
    return [x for x in terms(question) if len(x) >= 4 and not x.startswith(_GENERIC)]

# Fault reports (formerly support_request/support_candidates) live in app/support.py.

def coherent_candidates(db, question, parts, when=None):
    """Prefer a clearly anchored source for generic subquestions, never merge versions."""
    from .sufficiency import supports
    from .procedures import staged, stage
    # Independently specific subquestions must retain their separate sources.
    if not staged(question) and len(parts)>1 and any(focus_terms(part) for part in parts[1:]):
        return None
    # No parts (a query of punctuation or digits only): the question itself.
    focus=focus_terms(question if staged(question) or len(parts)<=1 else parts[0])
    if len(focus)<2: return None
    # Document choice aggregates lexical evidence across many fragments.
    hits=imported(db,question,k=100,gate=False)
    grouped={}
    for row in hits: grouped.setdefault((row['source_id'],row['version']),[]).append(row)
    if staged(question):
        from .procedures import version_units, question_events
        cache = {}
        events = question_events(question)
        def staged_units(key):
            if key not in cache:
                cache[key] = [u for u in version_units(db, *key, events=events) if u['stage'] and (u['facets'] or u['condition'] or u['marked'] or u['step'])]
            return cache[key]
        grouped = {key: rows for key, rows in grouped.items() if staged_units(key)}
    if grouped:
        # Versions repealed or not yet in force on the date (by their own texts)
        # do not compete for the answer.
        from .requisites import status as version_status
        states = version_status(db, list(grouped), when)
        grouped = {key: rows for key, rows in grouped.items() if states[key][0] in ('ok', 'uncertain')}
    if not grouped: return None
    def strength(rows):
        words=re.findall(r'[а-яa-z0-9]+',' '.join(r['text'] for r in rows).lower().replace('ё','е'))
        return sum(any(w.startswith(t) for w in words) for t in set(focus))
    def staging(key):
        """(the staged units name the question's event, number of stages)."""
        if not staged(question): return (0, 0)
        found = staged_units(key)
        named = any(re.search(r'\b' + re.escape(e), u['text'].lower().replace('ё','е')) for u in found for e in events)
        return (int(named), len({u['stage'] for u in found}))
    scored=sorted((((strength(rows),) + staging(key),key,rows) for key,rows in grouped.items()),key=lambda x:x[0],reverse=True)
    rank,key,rows=scored[0]
    # One matching word suffices when the document alone names the event of
    # the stages, or alone has both stages.
    minimum = 1 if staged(question) and (len(scored) == 1 or rank[1:] > scored[1][0][1:]) else 2
    if rank[0] < minimum or (len(scored)>1 and rank <= scored[1][0]): return None
    if len(rows)<2 and not staged(question): return None
    # Without curated dates one cannot choose between versions of a source.
    if db.execute('SELECT count(*) FROM imports WHERE source_id=?',(key[0],)).fetchone()[0] != 1: return None
    if staged(question):
        # Units of the whole selected version: the stage can be carried by a
        # heading and the form or deadline can sit on another page.
        from .procedures import select_units
        return select_units(staged_units(key))
    # Preserve the existing sheet-cell isolation before expanding a source.
    rows = filter_sibling_cells(rows, question)
    # Relevance checks still apply for requests about cost, cancellation, etc.
    return [r for r in rows if len(r['text']) <= 12000 and supports(question,r['text']) and not heading_only(db, r)][:3]

def filter_sibling_cells(rows, question):
    """Avoid treating sheet-name-only matches as answers for adjacent cells."""
    import json
    if not rows:
        return rows
    first = rows[0]
    first_meta = json.loads(first['metadata'])
    if first_meta.get('type') != 'cell':
        return rows
    first_row = re.search(r'\d+$', first_meta.get('range', ''))
    if not first_row:
        return rows
    query_terms = terms(question)
    filtered = [first]
    for row in rows[1:]:
        meta = json.loads(row['metadata'])
        other_row = re.search(r'\d+$', meta.get('range', ''))
        same_row = (meta.get('type') == 'cell' and other_row and
                    row['source_id'] == first['source_id'] and row['version'] == first['version'] and
                    meta.get('sheet') == first_meta.get('sheet') and other_row.group() == first_row.group())
        if same_row:
            cell_text = str(meta.get('formula') or meta.get('value') or '').lower().replace('ё', 'е')
            cell_terms = re.findall(r'[а-яёa-z0-9]+', cell_text)
            if not any(word.startswith(term) for word in cell_terms for term in query_terms):
                continue
        filtered.append(row)
    return filtered

def context(db, fragment_id, attachment_versions=None):
    """Return the selected fragment, linked footnotes and confirmed attachment versions."""
    row=db.execute('SELECT * FROM import_fragments WHERE id=?',(fragment_id,)).fetchone()
    if row is None: return []
    import json
    meta=json.loads(row['metadata'])
    result=[dict(row)]
    for fid in meta.get('footnote_ids', []):
        note = db.execute('SELECT * FROM import_fragments WHERE source_id=? AND version=? AND locator=?',
                          (row['source_id'], row['version'], 'footnote/' + str(fid))).fetchone()
        if note is not None:
            result.append(dict(note))
    try:
        relations=db.execute("SELECT target,basis FROM evidence_relations WHERE kind='attachment' AND origin=?",(row['source_id'],)).fetchall()
        for relation in relations:
            version = (attachment_versions or {}).get(relation['target'])
            if not version: continue  # A relation alone does not establish an applicable version.
            for r in db.execute('SELECT * FROM import_fragments WHERE source_id=? AND version=? ORDER BY rowid',(relation['target'],version)):
                entry=dict(r)
                entry['attachment_basis']=relation['basis']
                result.append(entry)
    except sqlite3.OperationalError as exc:
        if 'no such table: evidence_relations' not in str(exc): raise
    return result
