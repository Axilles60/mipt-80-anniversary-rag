"""Retrieval confidence vs the expected outcome (diagnostics for п. 47, synthetic corpus).

  python eval/confidence_scan.py --split tuning

For every question: the best cross-encoder score among the top chunks and the
best dense z-score. Prints how many answerable questions and how many
no-answer questions fall below each threshold, so a refusal threshold can be
chosen on tuning before any generation.
"""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'eval'))
import measure_rag  # noqa: E402


def main():
    p = argparse.ArgumentParser(); p.add_argument('--split', default='tuning'); p.add_argument('--out')
    args = p.parse_args()
    cases = json.loads((measure_rag.FIXTURES / f'{args.split}.json').read_text(encoding='utf-8'))
    os.environ['NAVIGATOR_RETRIEVAL'] = 'hybrid'; os.environ['NAVIGATOR_RERANK'] = '1'
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); manifest = root / 'manifest.json'; manifest.write_text('[]')
        paths, _ = measure_rag.build(root)
        db_path = root / 'db'
        subprocess.run([sys.executable, '-m', 'app', 'import', *paths, '--manifest', str(manifest)],
                       env=dict(os.environ, NAVIGATOR_DB=str(db_path)), cwd=ROOT, check=True, capture_output=True)
        from app.core import connect
        from app import chunk_search, dense
        rows = []
        with closing(connect(str(db_path))) as db:
            for c in cases:
                top = chunk_search.search(db, c['question'], k=8, when=c.get('date') or None)
                best = max((t.get('rerank', 0) for t in top), default=0.0)
                scored = dense.rank(db, c['question'], 'chunks') or []
                z = max((s[2] for s in scored if s[2] is not None), default=0.0)
                rows.append({'id': c['id'], 'type': c['type'], 'expect': c['expect'], 'rerank': round(best, 4), 'z': round(z, 2)})
    if args.out:
        Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
    answerable = [r for r in rows if r['expect'] in ('answer', 'partial')]
    refusals = [r for r in rows if r['expect'] in ('insufficient', 'off_topic')]
    print(f'answerable {len(answerable)}, no-answer {len(refusals)}')
    for key, thresholds in (('rerank', [0.001, 0.003, 0.005, 0.01, 0.02, 0.05, 0.1]), ('z', [2.0, 2.5, 3.0, 3.5, 4.0])):
        for t in thresholds:
            lost = [r['id'] for r in answerable if r[key] < t]
            caught = sum(1 for r in refusals if r[key] < t)
            print(f'{key} < {t}: no-answer caught {caught}/{len(refusals)}, answerable lost {len(lost)} {lost[:12]}')


if __name__ == '__main__':
    main()
