"""Compare answer pipelines on the fictional regulation corpus fixtures/rag (stage R0).

Documents are imported with the standard CLI into a new temporary database;
each configuration (environment flags) starts the standard web service and
asks every question through POST /ask. Same data, same questions, same k.

Retrieval (in-process, unit = imported fragment, or chunk when available):
  Recall@1/3/5/10 over labelled units, MRR, nDCG@5.
Answers:
  correct — expected status, every fact pattern in the answer text, an
            expected unit cited, nothing forbidden;
  grounding — share of expected units that are cited (the right point, not
            just the right answer);
  citation precision — cited units that contain an expected unit / all cited;
  false answers — cites only other units, forbidden text, or anything cited
            for a question without an answer / off topic;
  missed — refusal although an answer is expected;
  unsupported claims — answer items whose numbers, form codes, e-mail
            addresses or URLs do not occur in the cited text (faithfulness
            proxy, computed by code);
  off-topic recognised — the service says the question is outside the base;
  latency p50/p95.

  python eval/measure_rag.py --split tuning --config base= --config hybrid=NAVIGATOR_RETRIEVAL=hybrid \
      --report eval/rag_results/<new>.json
A configuration is name=VAR=value;VAR=value. Reports (JSON + HTML error
viewer) are never overwritten.
"""
import argparse
from contextlib import closing
import html
import json
import math
import os
from pathlib import Path
import re
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'eval'))
from synthetic_docs import write_docx, write_pdf, write_xlsx  # noqa: E402
from web_harness import Items  # noqa: E402

FIXTURES = ROOT / 'fixtures' / 'rag'
OFF_TOPIC = re.compile(r'не относится к|вне (?:темы|базы)|не по теме', re.I)
REFUSAL = 'Недостаточно данных'
CLAIM_TOKENS = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+|\b[\w-]+\.(?:invalid|ru|com|org)\b[/\w.-]*|'
                          r'\b[А-ЯA-Z]{1,4}-\d+\b|\b\d{1,2}:\d{2}\b|\b\d+(?:[.,]\d+)?\b')


def norm(text):
    return re.sub(r'[\s­]+', ' ', text.lower().replace('ё', 'е')).strip()


def docx_table(path, paragraphs, rows):
    from xml.sax.saxutils import escape
    ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    body = ''.join('<w:p><w:r><w:t xml:space="preserve">' + escape(p) + '</w:t></w:r></w:p>' for p in paragraphs)
    body += '<w:tbl>' + ''.join('<w:tr>' + ''.join('<w:tc><w:p><w:r><w:t>' + escape(str(c)) + '</w:t></w:r></w:p></w:tc>'
                                                   for c in row) + '</w:tr>' for row in rows) + '</w:tbl>'
    import zipfile
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('word/document.xml', '<w:document xmlns:w="' + ns + '"><w:body>' + body + '</w:body></w:document>')
    return path


def build(root):
    """Write the corpus; return file paths and {key: normalised needle}."""
    corpus = json.loads((FIXTURES / 'corpus.json').read_text(encoding='utf-8'))
    paths, needles = [], {}
    def cell(value):
        if isinstance(value, dict):
            needles[value['key']] = norm(value['needle']); return value['needle']
        return value
    for doc in corpus['documents']:
        path = root / doc['file']
        items = doc.get('paragraphs') or [p for page in doc.get('pages', []) for p in page]
        for p in items:
            needles[p['key']] = norm(p.get('needle', p['text']))
        if doc['kind'] == 'pdf':
            write_pdf(path, [[p['text'] for p in page] for page in doc['pages']])
        elif doc['kind'] == 'docx':
            write_docx(path, [(p['style'], p['text']) if p.get('style') else p['text'] for p in doc['paragraphs']])
        elif doc['kind'] == 'docx_table':
            docx_table(path, [p['text'] for p in doc['paragraphs']], [[cell(c) for c in row] for row in doc['rows']])
        else:
            write_xlsx(path, [[cell(c) for c in row] for row in doc['rows']], doc.get('title', 'Лист1'))
        paths.append(str(path))
    return paths, needles


def keys_in(text, needles):
    t = norm(text)
    return sorted(k for k, n in needles.items() if n and n in t)


def ranking(cases, ranked, unit_keys):
    positives = [c for c in cases if c['expected'] and c['expect'] in ('answer', 'partial')]
    total = sum(len(c['expected']) for c in positives)
    recall = {k: 0 for k in (1, 3, 5, 10)}; rr = 0.0; ndcg = 0.0
    for c in positives:
        ids, exp = ranked[c['id']], set(c['expected'])
        for k in recall:
            recall[k] += len({x for u in ids[:k] for x in unit_keys.get(u, [])} & exp)
        hits = [bool(set(unit_keys.get(u, [])) & exp) for u in ids]
        rr += next((1 / (i + 1) for i, h in enumerate(hits) if h), 0.0)
        dcg = sum(1 / math.log2(i + 2) for i, h in enumerate(hits[:5]) if h)
        ideal = sum(1 / math.log2(i + 2) for i in range(min(5, len(exp))))
        ndcg += dcg / ideal if ideal else 0
    n = len(positives) or 1
    return {**{f'recall@{k}': round(v / total, 3) for k, v in recall.items()},
            'mrr': round(rr / n, 3), 'ndcg@5': round(ndcg / n, 3), 'positive_cases': len(positives)}


def start(env):
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); env['PORT'] = str(s.getsockname()[1])
    server = subprocess.Popen([sys.executable, '-m', 'app'], env=env, cwd=ROOT,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = 'http://127.0.0.1:' + env['PORT']
    for _ in range(600):
        try:
            urlopen(base + '/', timeout=1).close(); return server, base
        except OSError:
            if server.poll() is not None: break
            time.sleep(.1)
    server.terminate(); raise RuntimeError('server unavailable')


def unit_text(db, fragment_id, at=None):
    row = db.execute('SELECT text FROM import_fragments WHERE id=?', (fragment_id,)).fetchone()
    if row is None: return ''
    text = row['text'] or ''
    if at and re.fullmatch(r'\d+-\d+', at):
        s, e = map(int, at.split('-'))
        if 0 <= s < e <= len(text): return text[s:e]
    return text


def answers(cases, env, db, needles):
    server, base = start(env)
    out = []
    try:
        for c in cases:
            started = time.perf_counter()
            data = {'question': c['question'], 'date': c.get('date', '')}
            page = urlopen(base + '/ask', data=urlencode(data).encode(), timeout=600).read().decode()
            seconds = time.perf_counter() - started
            parsed = Items(); parsed.feed(page)
            body = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', page.split('</form>', 1)[-1])))
            cited, unsupported, claims = [], [], 0
            for item in parsed.items:
                texts, rows = [], {}
                for href in item['hrefs']:
                    q = parse_qs(urlsplit(href).query)
                    fid = q.get('id', [''])[0]
                    unit = unit_text(db, fid, q.get('at', [None])[0])
                    texts.append(unit)
                    # Cells of one sheet row are one citation (the row), not one per cell.
                    row = re.match(r'(.*#sheet/[^/]+/cell/)[A-Z]+(\d+)$', fid)
                    if row:
                        key = row.group(1) + row.group(2)
                        if key in rows:
                            i = rows[key]; cited[i] = (cited[i][0], cited[i][1] + ' ' + unit); continue
                        rows[key] = len(cited)
                    cited.append((fid, unit))
                if not texts or item['text'].strip().startswith(('Ограничение:', 'Источник:', 'Применимость')):
                    continue
                claims += 1
                support = norm(' '.join(unit_text(db, parse_qs(urlsplit(h).query).get('id', [''])[0]) for h in item['hrefs']))
                claim = re.sub(r'\b[A-Z][\w-]*@[0-9a-f]{8,}#\S+', ' ', item['text'])   # drop printed fragment IDs
                missing = [t for t in CLAIM_TOKENS.findall(claim) if norm(t) not in support]
                if missing: unsupported.append({'text': item['text'][:200], 'missing': missing[:5]})
            cited_keys = [keys_in(u, needles) for _, u in cited]
            exp = set(c['expected'])
            relevant = [k for k in cited_keys if set(k) & exp]
            grounded = {x for k in cited_keys for x in k} & exp
            facts = [f for f in c['facts'] if re.search(f, body, re.I)]
            forbidden = [f for f in c['forbidden'] if re.search(f, body, re.I)]
            off_topic = bool(OFF_TOPIC.search(body))
            gap = REFUSAL in body
            if c['expect'] in ('insufficient', 'off_topic'):
                verdict = 'false_answer' if cited else ('correct' if c['expect'] == 'insufficient' or off_topic else 'refused_not_off_topic')
            elif not cited:
                verdict = 'missed'
            elif not relevant or forbidden:
                verdict = 'false_answer'
            elif len(facts) < len(c['facts']) or (c['expect'] == 'partial' and not gap):
                verdict = 'incomplete'
            else:
                verdict = 'correct'
            out.append({'id': c['id'], 'type': c['type'], 'question': c['question'], 'expect': c['expect'], 'verdict': verdict,
                        'grounding': f'{len(grounded)}/{len(exp)}', 'cited_keys': cited_keys, 'facts_missing': [f for f in c['facts'] if f not in facts],
                        'forbidden_found': forbidden, 'unsupported': unsupported, 'claims': claims, 'off_topic_said': off_topic,
                        'seconds': round(seconds, 3), 'answer': body[:1200]})
    finally:
        server.terminate(); server.wait(timeout=30)
    expecting = [r for r in out if r['expect'] in ('answer', 'partial')]
    cited_total = sum(len(r['cited_keys']) for r in expecting)
    relevant_total = sum(sum(1 for k in r['cited_keys'] if set(k) & set(c['expected'])) for r, c in zip(out, cases) if r in expecting)
    ground_num = sum(int(r['grounding'].split('/')[0]) for r in expecting)
    ground_den = sum(int(r['grounding'].split('/')[1]) for r in expecting)
    secs = sorted(r['seconds'] for r in out)
    summary = {'questions': len(out), 'correct': sum(r['verdict'] == 'correct' for r in out),
               'incomplete': sum(r['verdict'] == 'incomplete' for r in out),
               'false_answers': sum(r['verdict'] == 'false_answer' for r in out),
               'missed': sum(r['verdict'] == 'missed' for r in out),
               'grounding': round(ground_num / ground_den, 3) if ground_den else None,
               'citation_precision': round(relevant_total / cited_total, 3) if cited_total else None,
               'unsupported_claims': f"{sum(len(r['unsupported']) for r in out)}/{sum(r['claims'] for r in out)}",
               'refusals': f"{sum(r['verdict'] == 'correct' for r in out if r['expect'] == 'insufficient')}/{sum(r['expect'] == 'insufficient' for r in out)}",
               'off_topic_recognised': f"{sum(r['verdict'] == 'correct' for r in out if r['expect'] == 'off_topic')}/{sum(r['expect'] == 'off_topic' for r in out)}",
               'p50_s': round(statistics.median(secs), 3) if secs else None,
               'p95_s': round(secs[min(len(secs) - 1, int(len(secs) * .95))], 3) if secs else None}
    by_type = {}
    for r in out:
        row = by_type.setdefault(r['type'], {'n': 0, 'correct': 0, 'incomplete': 0, 'false_answer': 0, 'missed': 0, 'other': 0})
        row['n'] += 1
        row[r['verdict'] if r['verdict'] in row else 'other'] += 1
    summary['by_type'] = dict(sorted(by_type.items()))
    return summary, out


def viewer(report):
    rows = []
    for name, entry in report['configs'].items():
        rows.append(f'<h2>{html.escape(name)}</h2><pre>{html.escape(json.dumps(entry["summary"], ensure_ascii=False, indent=1))}</pre>'
                    '<table><tr><th>ID</th><th>Тип</th><th>Вопрос</th><th>Ожидание</th><th>Вердикт</th><th>Grounding</th>'
                    '<th>Нет фактов</th><th>Неподтверждено</th><th>Ответ</th></tr>')
        for r in entry['answers']:
            bad = r['verdict'] != 'correct'
            rows.append(f'<tr class="{"bad" if bad else ""}"><td>{r["id"]}</td><td>{r["type"]}</td><td>{html.escape(r["question"])}</td>'
                        f'<td>{r["expect"]}</td><td>{r["verdict"]}</td><td>{r["grounding"]}</td>'
                        f'<td>{html.escape(", ".join(r["facts_missing"]))}</td><td>{len(r["unsupported"])}</td>'
                        f'<td><small>{html.escape(r["answer"][:500])}</small></td></tr>')
        rows.append('</table>')
    return ('<!doctype html><meta charset="utf-8"><title>Оценка RAG</title><style>body{font:14px sans-serif;margin:16px}'
            'table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:4px;vertical-align:top}'
            '.bad{background:#fff1f0}</style><h1>Оценка конвейеров (синтетика)</h1>' + ''.join(rows))


def quick(cases):
    """Deterministic stratified subset: the 1st, 4th, 7th… question of every type."""
    seen = {}
    out = []
    for c in cases:
        n = seen.get(c['type'], 0); seen[c['type']] = n + 1
        if n % 3 == 0: out.append(c)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--split', choices=['tuning', 'holdout', 'holdout2'], required=True)
    p.add_argument('--config', action='append', default=[], help='name=VAR=value;VAR=value')
    p.add_argument('--report', required=True)
    p.add_argument('--only', help='comma-separated question IDs')
    p.add_argument('--retrieval-only', action='store_true', help='ranking metrics only, no answers (fast A/B of search)')
    p.add_argument('--quick', action='store_true',
                   help='every 3rd question of each type (about a third of the set, all types kept) for fast iterations')
    args = p.parse_args()
    report_path = Path(args.report)
    if report_path.exists() or report_path.with_suffix('.html').exists():
        raise SystemExit('report exists: ' + args.report)
    cases = json.loads((FIXTURES / (args.split + '.json')).read_text(encoding='utf-8'))
    if args.only: cases = [c for c in cases if c['id'] in set(args.only.split(','))]
    if args.quick: cases = quick(cases)
    configs = {}
    for spec in args.config or ['base=']:
        name, _, rest = spec.partition('=')
        configs[name] = dict(item.split('=', 1) for item in rest.split(';') if '=' in item)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); manifest = root / 'manifest.json'; manifest.write_text('[]')
        paths, needles = build(root)
        db_path = root / 'db'
        env = dict(os.environ, NAVIGATOR_DB=str(db_path), HOST='127.0.0.1')
        for key in ('NAVIGATOR_MODEL', 'NAVIGATOR_RETRIEVAL', 'NAVIGATOR_PIPELINE', 'NAVIGATOR_IMPORT_DIR', 'NAVIGATOR_RERANK', 'NAVIGATOR_LLM_URL'): env.pop(key, None)
        subprocess.run([sys.executable, '-m', 'app', 'import', *paths, '--manifest', str(manifest)],
                       env=env, cwd=ROOT, check=True, capture_output=True)
        from app.core import connect
        from app import retrieval
        report = {'split': args.split, 'quick': args.quick, 'questions': [c['id'] for c in cases], 'data': 'synthetic', 'configs': {}}
        for name, flags in configs.items():
            saved = {k: os.environ.get(k) for k in flags}
            os.environ.update(flags); os.environ['NAVIGATOR_DB'] = str(db_path)
            with closing(connect(str(db_path))) as db:
                unit_keys = {r['id']: keys_in(r['text'] or '', needles) for r in db.execute('SELECT id, text FROM import_fragments')}
                ranked = {c['id']: [r['id'] for r in retrieval.imported(db, c['question'], k=10)] for c in cases}
                rank = ranking(cases, ranked, unit_keys)
                # The same questions over RAG chunks (stage R1–R2).
                from app import chunk_search, chunking
                chunking.ensure(db)
                chunk_keys = {r['id']: keys_in(r['text'] or '', needles) for r in db.execute('SELECT id, text FROM chunks WHERE level=2')}
                chunk_ranked, timings = {}, []
                for c in cases:
                    started = time.perf_counter()
                    chunk_ranked[c['id']] = [r['id'] for r in chunk_search.search(db, c['question'], k=10, when=c.get('date') or None)]
                    timings.append(time.perf_counter() - started)
                timings.sort()
                rank = {**{'fragments.' + k: v for k, v in rank.items()},
                        **{'chunks.' + k: v for k, v in ranking(cases, chunk_ranked, chunk_keys).items()},
                        'chunks.search_p50_s': round(timings[len(timings) // 2], 3) if timings else None}
                if args.retrieval_only:
                    summary, results = {}, []
                else:
                    summary, results = answers(cases, dict(env, **flags), db, needles)
            for k, v in saved.items():
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v
            report['configs'][name] = {'flags': flags, 'ranking': rank, 'summary': summary, 'answers': results}
            print(name, json.dumps({**rank, **summary}, ensure_ascii=False))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    report_path.with_suffix('.html').write_text(viewer(report), encoding='utf-8')


if __name__ == '__main__':
    main()
