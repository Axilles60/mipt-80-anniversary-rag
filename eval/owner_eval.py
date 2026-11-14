"""Owner-side evaluation on the owner's own database of originals (CASE-07).

The agent never runs this on originals; the owner runs it locally. The script
works on a COPY of the given database (hybrid mode writes a vector cache into
the database, and the manual-acceptance database must stay unchanged), starts
the standard web service on loopback for each retrieval mode and asks every
question of fixtures/owner_eval/questions.json.

Metrics per mode (sources are the manifest IDs given at import, e.g. SRC-05):
  ranking  — retrieval.imported(k): document-level Recall@1/3/5 over the
             expected sources and MRR of the first expected source;
  answers  — POST /ask: correct source cited, false answers (only other
             sources cited, or anything cited for a question without an
             answer), missed answers (refusal although an answer is expected),
             citation precision (cited fragments from expected sources / all
             cited), correct refusals, facts matched, forbidden statements
             matched, links that do not open.
facts/forbidden are loose patterns for a first pass only: the owner confirms
every answer against the original and fills `owner_verified` in the report.

  python eval/owner_eval.py --db data/index/navigator.sqlite3 \
      --report data/runs/owner-eval-YYYYMMDD.json [--modes bm25,hybrid] [--k 5]
Writes JSON and a Markdown checklist next to it (<report>.md). Existing
reports are never overwritten. No network beyond 127.0.0.1.
"""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode, urljoin
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'eval'))
from web_harness import Items  # noqa: E402

DEFAULT_QUESTIONS = ROOT / 'fixtures' / 'owner_eval' / 'questions.json'
REFUSAL = 'Недостаточно данных'


def copy_database(source, target):
    """Consistent copy through the SQLite backup API; the source is opened read-only."""
    with closing(sqlite3.connect(Path(source).resolve().as_uri() + '?mode=ro', uri=True)) as src, \
            closing(sqlite3.connect(target)) as dst:
        src.backup(dst)


def unique(seq):
    return list(dict.fromkeys(seq))


def ranking(db, cases, k):
    from app import retrieval
    totals = {1: 0, 3: 0, 5: 0}; expected_total = 0; rr = 0.0; n = 0; per_case = {}
    for case in cases:
        rows = retrieval.imported(db, case['question'], k=max(k, 5) * 4)
        sources = unique(r['source_id'] for r in rows)[:max(k, 5)]
        per_case[case['id']] = sources
        expected = set(case['expected_sources'])
        if not expected or case['expect'] == 'insufficient':
            continue
        n += 1; expected_total += len(expected)
        for cut in totals:
            totals[cut] += len(expected & set(sources[:cut]))
        rr += next((1 / (i + 1) for i, s in enumerate(sources) if s in expected), 0.0)
    metrics = {f'recall@{c}': round(v / expected_total, 3) if expected_total else None for c, v in totals.items()}
    metrics['mrr'] = round(rr / n, 3) if n else None
    metrics['cases'] = n
    return metrics, per_case


def start_server(env):
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); env['PORT'] = str(s.getsockname()[1])
    server = subprocess.Popen([sys.executable, '-m', 'app'], env=env, cwd=ROOT,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = 'http://127.0.0.1:' + env['PORT']
    for _ in range(150):
        try:
            urlopen(base + '/', timeout=1).close(); return server, base
        except OSError:
            time.sleep(.1)
    server.terminate(); raise RuntimeError('server unavailable')


def body_text(page):
    body = page.split('</form>', 1)[-1]
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', body))


def answers(cases, env, source_of):
    server, base = start_server(env)
    out = []
    try:
        for case in cases:
            started = time.perf_counter()
            page = urlopen(base + '/ask', data=urlencode({'question': case['question']}).encode(), timeout=300).read().decode()
            seconds = round(time.perf_counter() - started, 3)
            parsed = Items(); parsed.feed(page)
            cited = unique(fid for item in parsed.items for fid in item['links'])
            hrefs = unique(h for item in parsed.items for h in item['hrefs'])
            broken = 0
            for href in hrefs:
                try:
                    urlopen(urljoin(base, href), timeout=30).close()
                except OSError:
                    broken += 1
            text = body_text(page)
            sources = [source_of.get(fid, '?') for fid in cited]
            expected = set(case['expected_sources'])
            relevant = [s for s in sources if s in expected]
            if case['expect'] == 'insufficient':
                verdict = 'correct_refusal' if not cited else 'false_answer'
            elif not cited:
                verdict = 'missed'
            elif not relevant:
                verdict = 'false_answer'
            else:
                verdict = 'correct_source'
            facts = [f for f in case.get('facts', []) if re.search(f, text, re.I)]
            forbidden = [f for f in case.get('forbidden', []) if re.search(f, text, re.I)]
            out.append({'id': case['id'], 'ac': case['ac'], 'topic': case['topic'], 'question': case['question'],
                        'expect': case['expect'], 'verdict': verdict, 'cited_sources': unique(sources),
                        'cited_fragments': len(cited), 'relevant_fragments': len(relevant),
                        'gap_shown': REFUSAL in page, 'facts_found': f'{len(facts)}/{len(case.get("facts", []))}',
                        'facts_missing': [f for f in case.get('facts', []) if f not in facts],
                        'forbidden_found': forbidden, 'broken_links': broken, 'seconds': seconds,
                        'answer_excerpt': text[:600], 'owner_verified': None, 'owner_note': ''})
    finally:
        server.terminate(); server.wait(timeout=10)
    return out


def summarise(results):
    expecting = [r for r in results if r['expect'] != 'insufficient']
    negatives = [r for r in results if r['expect'] == 'insufficient']
    cited = sum(r['cited_fragments'] for r in expecting)
    facts_total = sum(int(r['facts_found'].split('/')[1]) for r in results)
    facts_found = sum(int(r['facts_found'].split('/')[0]) for r in results)
    return {'questions': len(results),
            'correct_source': sum(r['verdict'] == 'correct_source' for r in results),
            'false_answers': sum(r['verdict'] == 'false_answer' for r in results),
            'missed_answers': sum(r['verdict'] == 'missed' for r in results),
            'correct_refusals': f"{sum(r['verdict'] == 'correct_refusal' for r in negatives)}/{len(negatives)}",
            'partial_with_gap_shown': f"{sum(r['gap_shown'] for r in results if r['expect'] == 'partial')}/{sum(r['expect'] == 'partial' for r in results)}",
            'citation_precision': round(sum(r['relevant_fragments'] for r in expecting) / cited, 3) if cited else None,
            'facts_matched': f'{facts_found}/{facts_total}',
            'forbidden_matched': sum(bool(r['forbidden_found']) for r in results),
            'broken_links': sum(r['broken_links'] for r in results),
            'mean_seconds': round(sum(r['seconds'] for r in results) / len(results), 3) if results else None}


def checklist(report):
    lines = ['# Оценка навигатора на оригиналах — чек-лист владельца', '',
             f"База: `{report['database']}` (оценка выполнена на копии). Дата: {report['created']}. "
             'Автоматические отметки предварительные: подтвердите каждый ответ по оригиналу.', '']
    for mode, entry in report['modes'].items():
        lines += [f'## Режим `{mode}`', '', '```', json.dumps({**entry['ranking'], **entry['summary']}, ensure_ascii=False, indent=1), '```', '',
                  '| ID | AC | Ожидание | Вердикт | Источники | Факты | Домыслы | Проверено владельцем |', '| --- | --- | --- | --- | --- | --- | --- | --- |']
        for r in entry['answers']:
            lines.append(f"| {r['id']} | {r['ac']} | {r['expect']} | {r['verdict']} | {', '.join(r['cited_sources']) or '—'} | "
                         f"{r['facts_found']} | {len(r['forbidden_found'])} | ☐ |")
        lines.append('')
    return '\n'.join(lines) + '\n'


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--db', required=True, help='owner database (copied, never modified)')
    p.add_argument('--report', required=True)
    p.add_argument('--questions', default=str(DEFAULT_QUESTIONS))
    p.add_argument('--modes', default='bm25')
    p.add_argument('--k', type=int, default=5)
    p.add_argument('--only', help='comma-separated question IDs or topics')
    args = p.parse_args()
    report_path = Path(args.report)
    if report_path.exists() or report_path.with_suffix('.md').exists():
        raise SystemExit('report exists, choose a new file: ' + args.report)
    if not Path(args.db).is_file():
        raise SystemExit('database not found: ' + args.db)
    cases = json.loads(Path(args.questions).read_text(encoding='utf-8'))['questions']
    if args.only:
        wanted = set(args.only.split(','))
        cases = [c for c in cases if c['id'] in wanted or c['topic'] in wanted or c['ac'] in wanted]
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / 'navigator.sqlite3'
        copy_database(args.db, copy)
        env = dict(os.environ, NAVIGATOR_DB=str(copy), HOST='127.0.0.1'); env.pop('NAVIGATOR_MODEL', None)
        os.environ['NAVIGATOR_DB'] = str(copy)
        from app.core import connect
        report = {'database': str(Path(args.db).resolve()), 'created': time.strftime('%Y-%m-%d %H:%M'),
                  'questions_file': str(Path(args.questions)), 'k': args.k, 'modes': {}}
        for mode in [m for m in args.modes.split(',') if m]:
            os.environ['NAVIGATOR_RETRIEVAL'] = mode
            from app import dense
            with closing(connect(str(copy))) as db:
                source_of = {r['id']: r['source_id'] for r in db.execute('SELECT id, source_id FROM import_fragments')}
                rank, per_case = ranking(db, cases, args.k)
                state = dense.status()
            results = answers(cases, dict(env, NAVIGATOR_RETRIEVAL=mode), source_of)
            for r in results:
                r['top_sources'] = per_case[r['id']]
            report['modes'][mode] = {'status': state, 'ranking': rank, 'summary': summarise(results), 'answers': results}
            print(mode, json.dumps({**rank, **report['modes'][mode]['summary']}, ensure_ascii=False))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    report_path.with_suffix('.md').write_text(checklist(report), encoding='utf-8')
    print('Report saved locally:', report_path, 'and', report_path.with_suffix('.md'))


if __name__ == '__main__':
    main()
