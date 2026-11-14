"""Which expected units reach the RAG context? (diagnostics, synthetic corpus only)

  python eval/diagnose_context.py --split tuning --type multihop [--only T75,T76]

Imports fixtures/rag/corpus.json into a temporary database, builds the RAG
context for each question exactly as app/rag.py does (no generation), and prints
for every expected unit whether it is in the retrieved top-k, only in the
context (section sibling), or absent. Separates retrieval misses from
generation misses.
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
    p = argparse.ArgumentParser()
    p.add_argument('--split', default='tuning'); p.add_argument('--type'); p.add_argument('--only')
    p.add_argument('--show', action='store_true', help='print the context chunks')
    p.add_argument('--generate', action='store_true', help='also call the generator (NAVIGATOR_LLM_URL or local) and print raw JSON and verification')
    args = p.parse_args()
    cases = json.loads((measure_rag.FIXTURES / f'{args.split}.json').read_text(encoding='utf-8'))
    if args.type: cases = [c for c in cases if c['type'] == args.type]
    if args.only: cases = [c for c in cases if c['id'] in set(args.only.split(','))]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); manifest = root / 'manifest.json'; manifest.write_text('[]')
        paths, needles = measure_rag.build(root)
        db_path = root / 'db'
        env = dict(os.environ, NAVIGATOR_DB=str(db_path))
        subprocess.run([sys.executable, '-m', 'app', 'import', *paths, '--manifest', str(manifest)],
                       env=env, cwd=ROOT, check=True, capture_output=True)
        os.environ.setdefault('NAVIGATOR_RETRIEVAL', 'hybrid')
        from app.core import connect
        from app import chunk_search, rag
        with closing(connect(str(db_path))) as db:
            totals = {'top': 0, 'context': 0, 'absent': 0}
            for c in cases:
                top = chunk_search.search(db, c['question'], k=rag.RETRIEVE_K, when=c.get('date') or None)
                ctx, _ = rag.build_context(db, c['question'], c.get('date') or None)
                top_keys = {k for ch in top for k in measure_rag.keys_in(ch['text'], needles)}
                ctx_keys = {k for ch in ctx for k in measure_rag.keys_in(ch['text'], needles)}
                marks = []
                for k in c['expected']:
                    where = 'top' if k in top_keys else 'context' if k in ctx_keys else 'absent'
                    totals[where] += 1; marks.append(f'{k}:{where}')
                print(c['id'], ' '.join(marks), '|', c['question'][:70])
                if args.show:
                    for n, ch in enumerate(ctx, 1):
                        print(f'   [{n}] {measure_rag.keys_in(ch["text"], needles)} {ch["text"][:110]}')
                if args.generate and ctx:
                    _, headers = rag.build_context(db, c['question'], c.get('date') or None)
                    messages = [{'role': 'system', 'content': rag.SYSTEM},
                                {'role': 'user', 'content': rag.prompt(c['question'], ctx, headers, c.get('date') or None)}]
                    model = rag.llm()
                    raw = json.loads(model.complete(messages))
                    for ch in ctx:          # as rag.answer: headers, question, synonym judge
                        ch['header'] = headers.get((ch['source_id'], ch['version']), '')
                    kept, dropped, short = rag.verify(raw, ctx, c['question'],
                                                      judge=lambda q, w, f: rag.same_subject(model, q, w, f))
                    print('   raw:', json.dumps(raw, ensure_ascii=False)[:600])
                    print('   kept:', [k['text'][:80] for k in kept], '| dropped:', dropped)
            print('expected units:', totals)


if __name__ == '__main__':
    main()
