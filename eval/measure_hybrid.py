"""Compare BM25, dense and hybrid retrieval on the fictional corpus fixtures/hybrid.

Documents are imported with the standard CLI into a new temporary database.
Two levels are measured on the same data, k and settings:

* ranking: ``retrieval.imported(db, question, k=5)`` under each mode —
  Recall@1/3/5 over the labelled paragraphs, MRR and nDCG@5 (binary relevance;
  a fragment is relevant if it contains a labelled paragraph);
* answers: the real ``POST /ask`` of a server started with the mode — correct
  answers, false answers (citing only unrelated fragments, or anything for a
  question without an answer), missed answers, citation precision, correct
  refusals and links that do not open.

Usage (from the project root):
  python eval/measure_hybrid.py --split tuning --report eval/hybrid_results/<new>.json
  python eval/measure_hybrid.py --split tuning --scores   # dense score table for calibration
The report path must not exist: earlier reports are never overwritten.
"""
import argparse
from contextlib import closing
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode, urljoin
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'eval'))
from synthetic_docs import write_docx, write_pdf, write_xlsx  # noqa: E402
from web_harness import Items  # noqa: E402

FIXTURES = ROOT / 'fixtures' / 'hybrid'
K = 5


def norm(text):
    return re.sub(r'[\s­]+', '', text.lower().replace('ё', 'е'))


def build(root):
    corpus = json.loads((FIXTURES / 'corpus.json').read_text(encoding='utf-8'))
    paths, keys = [], {}
    for doc in corpus['documents']:
        path = root / doc['file']
        if 'paragraphs' in doc:
            write_docx(path, [(p['style'], p['text']) if p.get('style') else p['text'] for p in doc['paragraphs']])
            items = doc['paragraphs']
        elif 'pages' in doc:
            write_pdf(path, [[p['text'] for p in page] for page in doc['pages']])
            items = [p for page in doc['pages'] for p in page]
        else:
            write_xlsx(path, doc['rows'])
            items = []
        keys.update({p['key']: norm(p['text']) for p in items})
        paths.append(str(path))
    return paths, keys


def label(fragments, keys):
    return {f['id']: sorted(k for k, text in keys.items() if text in norm(f['text'])) for f in fragments}


def ranking_metrics(cases, ranked, labels):
    positives = [c for c in cases if c['expected']]
    recall = {1: 0, 3: 0, 5: 0}; total = sum(len(c['expected']) for c in positives); rr = 0.0; ndcg = 0.0
    for case in positives:
        ids = ranked[case['id']]
        relevant = [bool(set(labels.get(fid, [])) & set(case['expected'])) for fid in ids]
        for k in recall:
            found = {key for fid in ids[:k] for key in labels.get(fid, [])} & set(case['expected'])
            recall[k] += len(found)
        rr += next((1 / (i + 1) for i, hit in enumerate(relevant) if hit), 0.0)
        dcg = sum(1 / math.log2(i + 2) for i, hit in enumerate(relevant[:K]) if hit)
        ideal_count = min(K, len({fid for fid, ks in labels.items() if set(ks) & set(case['expected'])}))
        ideal = sum(1 / math.log2(i + 2) for i in range(ideal_count))
        ndcg += dcg / ideal if ideal else 0.0
    n = len(positives) or 1
    return {'positive_cases': len(positives), 'expected_paragraphs': total,
            **{f'recall@{k}': round(v / total, 3) for k, v in recall.items()},
            'mrr': round(rr / n, 3), 'ndcg@5': round(ndcg / n, 3)}


def start_server(env):
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); env['PORT'] = str(s.getsockname()[1])
    server = subprocess.Popen([sys.executable, '-m', 'app'], env=env, cwd=ROOT,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = 'http://127.0.0.1:' + env['PORT']
    for _ in range(100):
        try: urlopen(base + '/', timeout=1).close(); return server, base
        except OSError: time.sleep(.1)
    server.terminate(); raise RuntimeError('server unavailable')


def answers(cases, env, labels):
    server, base = start_server(env)
    out = []
    try:
        for case in cases:
            started = time.perf_counter()
            page = urlopen(base + '/ask', data=urlencode({'question': case['question']}).encode(), timeout=120).read().decode()
            elapsed = time.perf_counter() - started
            parsed = Items(); parsed.feed(page)
            hrefs = list(dict.fromkeys(h for item in parsed.items for h in item['hrefs']))
            cited = list(dict.fromkeys(fid for item in parsed.items for fid in item['links']))
            broken = 0
            for href in hrefs:
                try: urlopen(urljoin(base, href), timeout=10).close()
                except OSError: broken += 1
            relevant = [fid for fid in cited if set(labels.get(fid, [])) & set(case['expected'])]
            refused = not cited
            if case['expected']:
                verdict = 'missed' if refused else ('correct' if relevant else 'false_answer')
            else:
                verdict = 'correct_refusal' if refused else 'false_answer'
            out.append({'id': case['id'], 'question': case['question'], 'verdict': verdict,
                        'cited': [labels.get(fid) or [fid.split('#')[-1]] for fid in cited],
                        'irrelevant_citations': len(cited) - len(relevant), 'broken_links': broken,
                        'insufficient_message': 'Недостаточно данных' in page, 'seconds': round(elapsed, 3)})
    finally:
        server.terminate(); server.wait(timeout=10)
    cited_total = sum(len(r['cited']) for r in out)
    irrelevant = sum(r['irrelevant_citations'] for r in out)
    negatives = [c for c in cases if not c['expected']]
    return {'correct_answers': sum(r['verdict'] == 'correct' for r in out),
            'false_answers': sum(r['verdict'] == 'false_answer' for r in out),
            'missed_answers': sum(r['verdict'] == 'missed' for r in out),
            'correct_refusals': f"{sum(r['verdict'] == 'correct_refusal' for r in out)}/{len(negatives)}",
            'citation_precision': round((cited_total - irrelevant) / cited_total, 3) if cited_total else None,
            'broken_links': sum(r['broken_links'] for r in out),
            'mean_seconds': round(sum(r['seconds'] for r in out) / len(out), 3),
            'results': out}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--split', choices=['tuning', 'holdout'], required=True)
    p.add_argument('--modes', default='bm25,dense,hybrid')
    p.add_argument('--report')
    p.add_argument('--scores', action='store_true', help='print dense cosine of best relevant/irrelevant fragment')
    p.add_argument('--skip-web', action='store_true')
    args = p.parse_args()
    if args.report and Path(args.report).exists():
        raise SystemExit('report exists, choose a new file: ' + args.report)
    cases = json.loads((FIXTURES / (args.split + '.json')).read_text(encoding='utf-8'))
    modes = [m for m in args.modes.split(',') if m]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); db_path = root / 'db'; manifest = root / 'manifest.json'; manifest.write_text('[]')
        paths, keys = build(root)
        env = dict(os.environ, NAVIGATOR_DB=str(db_path), HOST='127.0.0.1'); env.pop('NAVIGATOR_MODEL', None)
        subprocess.run([sys.executable, '-m', 'app', 'import', *paths, '--manifest', str(manifest)],
                       env=env, cwd=ROOT, check=True, capture_output=True)
        os.environ['NAVIGATOR_DB'] = str(db_path)
        from app.core import connect
        from app import dense, retrieval
        with closing(connect(str(db_path))) as db:
            fragments = [dict(r) for r in db.execute('SELECT * FROM import_fragments')]
            labels = label(fragments, keys)
            unlabelled = sorted(set(keys) - {k for ks in labels.values() for k in ks})
            if unlabelled: raise SystemExit('paragraphs not found in any fragment: ' + ', '.join(unlabelled))
            if args.scores:
                for case in cases:
                    ranked = dense.rank(db, case['question'])
                    if ranked is None: raise SystemExit('dense model unavailable: ' + str(dense.status()))
                    rel = [z for fid, _, z in ranked if set(labels.get(fid, [])) & set(case['expected'])]
                    irr = [z for fid, _, z in ranked if not set(labels.get(fid, [])) & set(case['expected'])]
                    best = f'{max(rel):5.2f}' if rel else '  -  '
                    print(f"{case['id']} z_relevant={best} z_other={max(irr):5.2f}  {case['question']}")
                return
            report = {'split': args.split, 'k': K, 'data': 'synthetic', 'cases': len(cases), 'modes': {}}
            for mode in modes:
                os.environ['NAVIGATOR_RETRIEVAL'] = mode
                state = dense.status()
                ranked = {c['id']: [r['id'] for r in retrieval.imported(db, c['question'], k=K)] for c in cases}
                negatives_with_candidates = sum(bool(ranked[c['id']]) for c in cases if not c['expected'])
                entry = {'status': state, 'ranking': ranking_metrics(cases, ranked, labels),
                         'negatives_with_candidates': negatives_with_candidates,
                         'top5': {cid: [labels.get(fid) or [fid.split('#')[-1]] for fid in ids] for cid, ids in ranked.items()}}
                if not args.skip_web:
                    entry['answers'] = answers(cases, dict(env, NAVIGATOR_RETRIEVAL=mode), labels)
                report['modes'][mode] = entry
    summary = {m: {**e['ranking'], **{k: v for k, v in e.get('answers', {}).items() if k != 'results'},
                   'negatives_with_candidates': e['negatives_with_candidates'], 'mode_used': e['status']['mode']}
               for m, e in report['modes'].items()}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    if args.report:
        target = Path(args.report); target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
