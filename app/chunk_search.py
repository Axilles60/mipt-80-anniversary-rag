"""Search over RAG chunks (sdd/rag-plan.md, stages R1–R2).

BM25 over the chunks' index text (FTS5) on the original question, dense
cosine over chunk vectors, reciprocal rank fusion — the same scheme and
z-score gates as for fragments (iterations 5–7). Retrieval mode follows
NAVIGATOR_RETRIEVAL (bm25 | dense | hybrid). Chunks of versions that are
repealed or not in force on the question's date are dropped (iteration 15).
"""
import json

from . import chunking, dense
from .retrieval import terms


def lexical(db, question, limit=100):
    tokens = terms(question)
    if not tokens:
        return []
    query = ' OR '.join('"' + t + '"*' for t in tokens)
    return [r[0] for r in db.execute('SELECT id FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts), id LIMIT ?',
                                     (query, limit))]


def ranked_ids(db, question, gate=True):
    chunking.ensure(db)
    lex = lexical(db, question)
    mode = dense.mode()
    if mode == 'bm25' or not terms(question):
        return lex
    scored = dense.rank(db, question, 'chunks')
    if scored is None:
        return lex
    standout = [cid for cid, _, z in scored if z is not None and z >= dense.min_z()][:dense.DEPTH]
    if mode == 'dense':
        return standout
    z = {cid: value for cid, _, value in scored}
    if gate:
        lex = [cid for cid in lex if z.get(cid) is None or z[cid] >= dense.gate_z()]
    keep = set(lex) | set(standout)
    return dense.fuse(lex, [cid for cid, _, _ in scored if cid in keep])


def search(db, question, k=10, when=None):
    """Child chunks (dicts with meta) best first, applicable on ``when``."""
    ids = ranked_ids(db, question)
    if not ids:
        return []
    rows = {r['id']: dict(r) for r in db.execute(
        'SELECT * FROM chunks WHERE id IN (%s)' % ','.join('?' * len(ids[:200])), ids[:200])}
    out = [rows[i] for i in ids if i in rows]
    from .requisites import status
    states = status(db, {(r['source_id'], r['version']) for r in out}, when)
    out = [r for r in out if states[(r['source_id'], r['version'])][0] in ('ok', 'uncertain')]
    for r in out:
        r['meta'] = json.loads(r['meta'] or '{}')
    return out[:k]


def parent(db, chunk):
    row = db.execute('SELECT * FROM chunks WHERE id=?', (chunk['parent_id'],)).fetchone()
    return dict(row) if row else None
