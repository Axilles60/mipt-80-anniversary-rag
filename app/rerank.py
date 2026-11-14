"""Cross-encoder reranking of retrieved chunks (stage R3, sdd/rag-plan.md).

On in the RAG pipeline, off otherwise; NAVIGATOR_RERANK=1/0 overrides. The model is a local ONNX
cross-encoder (default data/models/bge-reranker-v2-m3: BAAI/bge-reranker-v2-m3,
int8, Apache-2.0), read from NAVIGATOR_RERANK_MODEL. It scores each
(question, chunk) pair jointly; the top RERANK_DEPTH candidates of the fused
BM25 + dense list are reordered by that score. If the model or onnxruntime is
missing, retrieval keeps the fused order. Nothing leaves the machine.
"""
import math
import os
from pathlib import Path
import threading

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = ROOT / 'data' / 'models' / 'bge-reranker-v2-m3'
RERANK_DEPTH = 20
MAX_TOKENS = 512
_lock = threading.Lock()
_models, _failures = {}, {}


def enabled():
    """NAVIGATOR_RERANK=1/0; unset — on in the RAG pipeline (its refusal by
    retrieval confidence needs the score), off otherwise."""
    value = os.environ.get('NAVIGATOR_RERANK', '').strip().lower()
    if not value:
        return os.environ.get('NAVIGATOR_PIPELINE', '').strip().lower() == 'rag'
    return value in ('1', 'on', 'true', 'yes')


def depth():
    try:
        return max(1, int(os.environ.get('NAVIGATOR_RERANK_DEPTH', RERANK_DEPTH)))
    except ValueError:
        return RERANK_DEPTH


def model_path():
    return Path(os.environ.get('NAVIGATOR_RERANK_MODEL') or DEFAULT_MODEL)


def _onnx_file(path):
    for name in ('model_int8.onnx', 'model_quantized.onnx', 'model.onnx'):
        if (path / name).is_file():
            return path / name
    return None


class CrossEncoder:
    def __init__(self, path):
        import numpy
        import onnxruntime
        from tokenizers import Tokenizer
        self.np = numpy
        model = _onnx_file(path)
        if model is None or not (path / 'tokenizer.json').is_file():
            raise FileNotFoundError('ONNX model or tokenizer.json not found in ' + str(path))
        tokenizer = Tokenizer.from_file(str(path / 'tokenizer.json'))
        tokenizer.enable_truncation(max_length=MAX_TOKENS, strategy='only_second')
        pad = tokenizer.token_to_id('<pad>')
        tokenizer.enable_padding(pad_id=1 if pad is None else pad, pad_token='<pad>')
        self.tokenizer = tokenizer
        options = onnxruntime.SessionOptions()
        options.log_severity_level = 3
        self.session = onnxruntime.InferenceSession(str(model), options, providers=['CPUExecutionProvider'])
        self.inputs = {i.name for i in self.session.get_inputs()}

    def scores(self, question, texts, batch=8):
        """Relevance in [0, 1] of each text to the question."""
        np = self.np
        out = []
        for start in range(0, len(texts), batch):
            encoded = self.tokenizer.encode_batch([(question, t) for t in texts[start:start + batch]])
            ids = np.array([e.ids for e in encoded], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
            feed = {'input_ids': ids, 'attention_mask': mask}
            if 'token_type_ids' in self.inputs:
                feed['token_type_ids'] = np.zeros_like(ids)
            logits = np.asarray(self.session.run(None, feed)[0]).reshape(len(encoded), -1)[:, 0]
            out.extend(1 / (1 + math.exp(-float(x))) for x in logits)
        return out


def model():
    """The loaded cross-encoder, or None (reason kept for status())."""
    path = model_path()
    key = str(path)
    with _lock:
        if key in _models:
            return _models[key]
        if key in _failures:
            return None
        try:
            _models[key] = CrossEncoder(path)
            return _models[key]
        except Exception as exc:  # missing libraries, missing or corrupt model
            _failures[key] = type(exc).__name__ + ': ' + str(exc)[:300]
            return None


def status():
    if not enabled():
        return 'off'
    return 'on' if model() is not None else 'unavailable: ' + _failures.get(str(model_path()), 'unknown')


def pair_text(chunk):
    meta = chunk.get('meta') or {}
    head = ' › '.join(x for x in (meta.get('document'), meta.get('section')) if x)
    return (head + '\n' if head else '') + chunk['text']


def rerank(question, chunks):
    """Reorder the first depth() chunks by cross-encoder score; the rest keep
    their order after them. Each scored chunk gets 'rerank' in [0, 1]."""
    enc = model() if enabled() else None
    if enc is None or not chunks:
        return chunks
    head, tail = chunks[:depth()], chunks[depth():]
    with _lock:
        values = enc.scores(question, [pair_text(c) for c in head])
    for c, v in zip(head, values):
        c['rerank'] = round(v, 4)
    order = sorted(range(len(head)), key=lambda i: (-values[i], i))
    return [head[i] for i in order] + tail
