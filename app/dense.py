"""Optional local dense retrieval: multilingual E5 through ONNX Runtime.

Off by default. ``NAVIGATOR_RETRIEVAL=dense`` or ``hybrid`` turns it on. The
model is read from ``NAVIGATOR_EMBED_MODEL`` (default
``data/models/multilingual-e5-small``) and is never downloaded at run time;
without the model or its libraries the service keeps BM25 and ``status()``
names the reason. Question and fragment texts never leave the process.

Fragment vectors are cached in the same SQLite file (``import_vectors``), keyed
by fragment ID and model, so newly imported documents are embedded on first use
without re-indexing the rest. ``python -m app embed`` fills the cache ahead of
time. A fragment longer than one chunk is scored by its best chunk.
"""
import os
import re
import sqlite3
import threading
from pathlib import Path

MODES = ('bm25', 'dense', 'hybrid')
DEFAULT_MODEL = Path(__file__).resolve().parent.parent / 'data' / 'models' / 'multilingual-e5-small'
CHUNK_CHARS = 900       # ~300 XLM-R tokens of Russian text, below the 512 limit
DEPTH = 20              # dense-only candidates passed to fusion
RRF_K = 60              # standard reciprocal-rank-fusion constant
# A fragment without any lexical match becomes a candidate only if its cosine
# stands out from the scores of the whole corpus for this question:
# z = (score - mean) / std >= MIN_Z. Absolute E5 cosines shift with wording
# (even a final "?" moves them by more than the gap between relevant and
# off-topic fragments); z is stable. Without the cut every question, including
# off-topic ones, would get "nearest" fragments. Calibrated on
# fixtures/hybrid/tuning.json only: off-topic questions peak at z<=2.4.
DEFAULT_MIN_Z = 2.5
MIN_POOL = 20           # below this many fragments z is unreliable: no dense-only candidates

_lock = threading.Lock()
_encoders = {}
_failures = {}
_matrices = {}


def mode():
    value = os.environ.get('NAVIGATOR_RETRIEVAL', 'bm25').strip().lower()
    return value if value in MODES else 'bm25'


def min_z():
    try:
        return float(os.environ.get('NAVIGATOR_DENSE_MIN_Z', DEFAULT_MIN_Z))
    except ValueError:
        return DEFAULT_MIN_Z


def model_path():
    return Path(os.environ.get('NAVIGATOR_EMBED_MODEL') or DEFAULT_MODEL)


class Encoder:
    def __init__(self, path):
        import numpy
        import onnxruntime
        from tokenizers import Tokenizer
        self.np = numpy
        model = path / 'model.onnx'
        tokenizer = Tokenizer.from_file(str(path / 'tokenizer.json'))
        tokenizer.enable_truncation(max_length=512)
        pad = tokenizer.token_to_id('<pad>')
        tokenizer.enable_padding(pad_id=1 if pad is None else pad, pad_token='<pad>')
        self.tokenizer = tokenizer
        options = onnxruntime.SessionOptions()
        options.log_severity_level = 3
        self.session = onnxruntime.InferenceSession(str(model), options, providers=['CPUExecutionProvider'])
        self.inputs = {i.name for i in self.session.get_inputs()}
        # The cache key changes when the model file is replaced.
        self.name = path.name + '@' + str(model.stat().st_size)

    def encode(self, texts, batch=16):
        np = self.np
        out = []
        for start in range(0, len(texts), batch):
            encoded = self.tokenizer.encode_batch(texts[start:start + batch])
            ids = np.array([e.ids for e in encoded], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
            feed = {'input_ids': ids, 'attention_mask': mask}
            if 'token_type_ids' in self.inputs:
                feed['token_type_ids'] = np.zeros_like(ids)
            hidden = self.session.run(None, feed)[0]
            weights = mask[..., None].astype(np.float32)
            vectors = (hidden * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
            vectors /= np.clip(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-9, None)
            out.append(vectors.astype(np.float32))
        return np.vstack(out) if out else np.zeros((0, 0), dtype=np.float32)


def encoder():
    """The loaded encoder, or None with the reason kept for status()."""
    path = model_path()
    key = str(path)
    with _lock:
        if key in _encoders:
            return _encoders[key]
        if key in _failures:
            return None
        try:
            if not (path / 'model.onnx').is_file() or not (path / 'tokenizer.json').is_file():
                raise FileNotFoundError('model.onnx or tokenizer.json not found in ' + key)
            _encoders[key] = Encoder(path)
            return _encoders[key]
        except Exception as exc:  # missing libraries, corrupt model, etc.
            _failures[key] = type(exc).__name__ + ': ' + str(exc)[:300]
            return None


def status():
    current = mode()
    if current == 'bm25':
        return {'mode': 'bm25'}
    enc = encoder()
    if enc is None:
        return {'mode': 'bm25', 'requested': current, 'fallback': _failures.get(str(model_path()), 'unavailable')}
    return {'mode': current, 'model': enc.name, 'min_z': min_z()}


def chunks(text):
    """Split on line and sentence boundaries into pieces of at most CHUNK_CHARS."""
    text = text.strip()
    if not text:
        return []
    pieces = [p for p in re.split(r'(?<=[.!?;])\s+|\n+', text) if p.strip()]
    out, current = [], ''
    for piece in pieces:
        piece = re.sub(r'\s+', ' ', piece).strip()
        while len(piece) > CHUNK_CHARS:
            if current:
                out.append(current); current = ''
            out.append(piece[:CHUNK_CHARS]); piece = piece[CHUNK_CHARS:]
        if current and len(current) + 1 + len(piece) > CHUNK_CHARS:
            out.append(current); current = ''
        current = (current + ' ' + piece).strip()
    if current:
        out.append(current)
    return out


SCHEMA = '''CREATE TABLE IF NOT EXISTS import_vectors (
    fragment_id TEXT, chunk INTEGER, model TEXT, vector BLOB,
    PRIMARY KEY(fragment_id, chunk, model))'''


def ensure_vectors(db, enc):
    """Embed fragments that have no cached vector for this model; return count."""
    try:
        db.execute(SCHEMA)
        writable = True
    except sqlite3.OperationalError:
        writable = False
    pending = db.execute(
        'SELECT f.id, f.text FROM import_fragments f WHERE NOT EXISTS '
        '(SELECT 1 FROM import_vectors v WHERE v.fragment_id=f.id AND v.model=?) ORDER BY f.rowid',
        (enc.name,)).fetchall() if writable else []
    if not pending:
        return 0
    rows, texts = [], []
    for fragment in pending:
        parts = chunks(fragment['text'] or '')
        if not parts:
            rows.append((fragment['id'], 0, None))  # marker: nothing to embed
        for n, part in enumerate(parts):
            rows.append((fragment['id'], n, len(texts)))
            texts.append('passage: ' + part)
    vectors = enc.encode(texts) if texts else None
    with db:
        db.executemany('INSERT OR REPLACE INTO import_vectors VALUES (?,?,?,?)',
                       [(fid, n, enc.name, None if i is None else vectors[i].tobytes()) for fid, n, i in rows])
    return len(pending)


def _matrix(db, enc):
    np = enc.np
    path = db.execute('PRAGMA database_list').fetchone()[2]
    count = db.execute('SELECT count(*) FROM import_vectors WHERE model=?', (enc.name,)).fetchone()[0]
    key = (path, enc.name)
    cached = _matrices.get(key)
    if cached and cached[0] == count:
        return cached[1], cached[2]
    data = db.execute(
        'SELECT v.fragment_id, v.vector FROM import_vectors v JOIN import_fragments f ON f.id=v.fragment_id '
        'WHERE v.model=? AND v.vector IS NOT NULL ORDER BY f.rowid, v.chunk', (enc.name,)).fetchall()
    ids = [r[0] for r in data]
    matrix = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in data]) if data else np.zeros((0, 1), np.float32)
    if path:
        _matrices[key] = (count, ids, matrix)
    return ids, matrix


def rank(db, question):
    """[(fragment_id, cosine, z)] best first, or None when dense search is unavailable.

    z is the fragment's best cosine standardised over all fragments; it is
    None when the corpus is smaller than MIN_POOL."""
    enc = encoder()
    if enc is None:
        return None
    try:
        ensure_vectors(db, enc)
        ids, matrix = _matrix(db, enc)
    except sqlite3.OperationalError:
        return None
    if not ids:
        return []
    # Wording noise such as a final question mark must not move the scores.
    text = re.sub(r'[\s?!.,;:…]+$', '', re.sub(r'\s+', ' ', question)).strip() or question
    query = enc.encode(['query: ' + text])[0]
    scores = matrix @ query
    best = {}
    for fid, score in zip(ids, scores.tolist()):
        if score > best.get(fid, -2.0):
            best[fid] = score
    values = list(best.values())
    mean = sum(values) / len(values)
    std = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
    reliable = len(values) >= MIN_POOL and std > 1e-6
    return sorted(((fid, s, (s - mean) / std if reliable else None) for fid, s in best.items()),
                  key=lambda item: (-item[1], item[0]))


def fuse(lexical_ids, dense_ids):
    """Reciprocal rank fusion of two ranked ID lists."""
    score = {}
    for ranked in (lexical_ids, dense_ids):
        for position, fid in enumerate(ranked, 1):
            score[fid] = score.get(fid, 0.0) + 1.0 / (RRF_K + position)
    lexical = set(lexical_ids)
    order = {fid: i for i, fid in enumerate(list(lexical_ids) + [x for x in dense_ids if x not in lexical])}
    return sorted(score, key=lambda fid: (-score[fid], order[fid]))
