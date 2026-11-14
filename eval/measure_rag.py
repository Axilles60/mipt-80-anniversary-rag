"""Compare answer pipelines on the fictional regulation corpus fixtures/rag (stage R0).

Documents are imported with the standard CLI into a new temporary database;
each configuration (environment flags) starts the standard web service and
asks every question through POST /ask. Same data, same questions, same k.

Retrieval (in-process, unit = imported fragment, or chunk when available):
  Recall@1/3/5/10 over labelled units, MRR, nDCG@5.
Answers (evaluator eval3, after the external audit of commit d23f0fc):
  asserted — what the service states as its answer: the short answer (h3),
            the cited items and the code-generated «Ограничение» notes. The
            «no data for part of the question» line, «Источник» requisites and
            the footer are not statements: facts found only there do not count;
  correct — expected status, every fact pattern in the asserted text, an
            expected unit cited, nothing forbidden asserted, no unsupported claim;
  grounding — share of expected units that are cited (the right point, not
            just the right answer);
  citation precision — cited units that contain an expected unit / all cited;
  false answers — cites only other units, forbidden text asserted, a claim or
            short answer with a number, code or address absent from its cited
            text, an answer with no citation at all, a statement that the
            documents say nothing although the expected fact exists, or any
            statement for a question without an answer / off topic;
  missed — refusal although an answer is expected;
  unsupported claims — claims and short answers whose numbers, form codes,
            e-mail addresses or URLs do not occur in the cited text (a narrow
            proxy: relations, negation and applicability are not checked);
  off-topic recognised — the service says the question is outside the base;
  latency p50/p95.

  python eval/measure_rag.py --split tuning --config base= --config hybrid=NAVIGATOR_RETRIEVAL=hybrid \\
      --report eval/rag_results/<new>.json
  python eval/measure_rag.py --split tuning --rescore eval/rag_results/<old>.json --report eval/rag_results/<new>.json
A configuration is name=VAR=value;VAR=value. Reports (JSON + HTML error
viewer) are never overwritten. Every answer keeps its full HTML («page») and a
hash of its gold record («gold»): --rescore scores saved pages again with the
current evaluator and labels, without the generator.
"""
import argparse
from contextlib import closing
import hashlib
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
NO_ANSWER = 'Недостаточно данных для ответа'
EVALUATOR = 'eval3'
# Lines of the answer page that are not statements of the answer.
SERVICE = ('Источник:', 'Ограничение:', 'Применимость', 'Самопроверка', 'Недостаточно данных')
PART_HEADING = re.compile(r'Документ \d+:')
GOLD_FIELDS = ('question', 'date', 'expect', 'expected', 'facts', 'forbidden', 'derived')
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


def fold(text):
    """ё → е: facts and forbidden patterns are written either way."""
    return text.replace('ё', 'е').replace('Ё', 'Е')


def has_token(token, text):
    """A number, code or address of a statement is one of the tokens of the cited text,
    split by the same pattern: 10 is not found in 20100, parts of 01.10.2023 are."""
    strip = lambda t: norm(t).rstrip('.,;:')
    return strip(token) in {strip(t) for t in CLAIM_TOKENS.findall(text)}


GAP = re.compile(r'Недостаточно данных для части вопроса:.*?(?=Самопроверка|Источник:|Применимость|$)', re.S)


def without_gap(body):
    """The answer without its «no data for part of the question: …» line: a forbidden
    statement must be asserted, not named as missing."""
    return GAP.sub(' ', body)


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
        # Gain only for an expected unit not seen higher up: three chunks of one
        # point are one relevant result, so nDCG stays within [0, 1].
        seen, gains = set(), []
        for u in ids[:5]:
            new = (set(unit_keys.get(u, [])) & exp) - seen
            gains.append(1 if new else 0); seen |= new
        dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
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
    parts = re.fullmatch(r'([^@]+)@[0-9a-f]+(#.+)', fragment_id or '')
    if row is None and parts:
        # A saved page from another build of the corpus (--rescore): the generated files
        # carry timestamps, so the version hash differs; the document (its name) and the
        # locator do not, and the evaluation base has one version per document.
        rows = db.execute("SELECT text FROM import_fragments WHERE id LIKE ? || '@%' AND substr(id, -length(?)) = ?",
                          (parts.group(1), parts.group(2), parts.group(2))).fetchall()
        row = rows[0] if len(rows) == 1 else None
    if row is None: return ''
    text = row['text'] or ''
    if at and re.fullmatch(r'\d+-\d+', at):
        s, e = map(int, at.split('-'))
        if 0 <= s < e <= len(text): return text[s:e]
    return text


def fact_pattern(fact):
    """A literal fact matches any case form of its words: «директором по информационным
    технологиям» is the fact «директор по информационным технологиям». Long words keep
    their stem (all but the last three letters, at least four); numbers, short words and
    facts written as regular expressions stay as they are."""
    if re.search(r'[\\\[\]|()?*+{}^$.]', fact):
        return fact
    return re.sub(r'[А-Яа-яЁё]{5,}', lambda w: w.group(0)[:max(4, len(w.group(0)) - 3)] + r'\w*', fact)


def gold_key(case):
    """Hash of the gold record: runs are comparable only on equal labels."""
    record = {k: case.get(k) for k in GOLD_FIELDS}
    return hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:12]


def plain(fragment):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', fragment))).strip()


def score(c, page, seconds, db, needles):
    """Verdict and metrics of one answer page (everything after the question form)."""
    from app.rag import ABSENCE
    answer_html = page.split('</form>', 1)[-1]
    parsed = Items(); parsed.feed(answer_html)
    body = plain(answer_html)
    shorts = [s for s in (plain(h) for h in re.findall(r'<h3>(.*?)</h3>', answer_html, re.S))
              if s and not PART_HEADING.match(s)]
    cited, unsupported, claims, detail, notes, uncited = [], [], [], [], [], []
    sources_line = ' '.join(i['text'] for i in parsed.items if i['text'].strip().startswith('Источник:'))
    for item in parsed.items:
        text = item['text'].strip()
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
        if text.startswith('Ограничение:'):
            notes.append(text); continue          # code-generated from the documents, cited
        if text.startswith(SERVICE):
            continue
        if not texts:
            if NO_ANSWER not in text and not OFF_TOPIC.search(text):
                uncited.append(text)              # a statement without any citation
            continue
        # The requisites the answer prints in «Источник» are visible to the reader too.
        support = ' '.join(unit_text(db, parse_qs(urlsplit(h).query).get('id', [''])[0]) for h in item['hrefs']) + ' ' + sources_line
        claim = re.sub(r'\b[A-Z][\w-]*@[0-9a-f]{8,}#\S+', ' ', text).strip()   # drop printed fragment IDs
        missing = [t for t in CLAIM_TOKENS.findall(claim) if not has_token(t, support)]
        if missing: unsupported.append({'text': claim[:200], 'missing': missing[:5]})
        claims.append(claim)
        detail.append({'text': claim[:400], 'keys': sorted({k for t in texts for k in keys_in(t, needles)})})
    # The short answer is a statement too: its tokens must occur in the cited text of the answer.
    all_cited = ' '.join(u for _, u in cited) + ' ' + sources_line
    for s in shorts:
        missing = [t for t in CLAIM_TOKENS.findall(s) if not has_token(t, all_cited)]
        if missing or not cited: unsupported.append({'text': s[:200], 'missing': missing[:5] or ['нет ссылки'], 'short': True})
    asserted = ' '.join(shorts + claims + notes)
    gap_text = ' '.join(m.group(0) for m in GAP.finditer(body))
    cited_keys = [keys_in(u, needles) for _, u in cited]
    exp = set(c['expected'])
    relevant = [k for k in cited_keys if set(k) & exp]
    grounded = {x for k in cited_keys for x in k} & exp
    facts = [f for f in c['facts'] if re.search(fold(fact_pattern(f)), fold(asserted), re.I)]
    forbidden = [f for f in c['forbidden'] if re.search(fold(f), fold(asserted), re.I)]
    # «The documents do not state X» asserted while the expected fact exists in them.
    denied = [s for s in shorts + claims if ABSENCE.search(s)]
    off_topic = bool(OFF_TOPIC.search(body))
    states = bool(shorts or claims)
    if c['expect'] in ('insufficient', 'off_topic'):
        if states or uncited or cited:
            verdict = 'false_answer'
        elif not (NO_ANSWER in body or off_topic):
            verdict = 'no_refusal'
        else:
            verdict = 'correct' if c['expect'] == 'insufficient' or off_topic else 'refused_not_off_topic'
    elif not states:
        verdict = 'missed'
    elif not cited or not relevant or forbidden or unsupported:
        verdict = 'false_answer'
    elif c['expect'] == 'answer' and denied and not facts and c['facts']:
        verdict = 'false_answer'
    elif len(facts) < len(c['facts']) or (c['expect'] == 'partial' and REFUSAL not in body):
        verdict = 'incomplete'
    else:
        verdict = 'correct'
    # Claims backed only by fragments without an expected unit: a «correct» answer may
    # still contain a false statement about a neighbouring subject (manual review).
    suspect = [d for d in detail if c['expect'] in ('answer', 'partial') and d['keys'] and not set(d['keys']) & exp]
    return {'id': c['id'], 'type': c['type'], 'question': c['question'], 'expect': c['expect'], 'gold': gold_key(c),
            'verdict': verdict, 'grounding': f'{len(grounded)}/{len(exp)}', 'cited_keys': cited_keys,
            'facts_missing': [f for f in c['facts'] if f not in facts], 'forbidden_found': forbidden,
            'unsupported': unsupported, 'claims': len(claims) + len(shorts), 'uncited': uncited, 'denied': denied,
            'gap': gap_text, 'off_topic_said': off_topic, 'seconds': round(seconds, 3), 'answer': body,
            'claims_detail': detail, 'suspect': suspect, 'page': answer_html}


def answers(cases, env, db, needles):
    server, base = start(env)
    out = []
    try:
        for c in cases:
            started = time.perf_counter()
            data = {'question': c['question'], 'date': c.get('date', '')}
            page = urlopen(base + '/ask', data=urlencode(data).encode(), timeout=600).read().decode()
            out.append(score(c, page, time.perf_counter() - started, db, needles))
    finally:
        server.terminate(); server.wait(timeout=30)
    return summarize(cases, out), out


def summarize(cases, out):
    expecting = [r for r in out if r['expect'] in ('answer', 'partial')]
    cited_total = sum(len(r['cited_keys']) for r in expecting)
    gold = {c['id']: c for c in cases}
    relevant_total = sum(sum(1 for k in r['cited_keys'] if set(k) & set(gold[r['id']]['expected'])) for r in expecting)
    ground_num = sum(int(r['grounding'].split('/')[0]) for r in expecting)
    ground_den = sum(int(r['grounding'].split('/')[1]) for r in expecting)
    secs = sorted(r['seconds'] for r in out)
    summary = {'questions': len(out), 'correct': sum(r['verdict'] == 'correct' for r in out),
               'incomplete': sum(r['verdict'] == 'incomplete' for r in out),
               'false_answers': sum(r['verdict'] == 'false_answer' for r in out),
               'missed': sum(r['verdict'] == 'missed' for r in out),
               'suspect_correct': sum(1 for r in out if r['verdict'] == 'correct' and r['suspect']),
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
    # The free text of «no data for part of the question» is not checked by the service:
    # answers whose gap names a number, code or address (a statement, not only a gap).
    summary['gap_with_tokens'] = sum(1 for r in out if CLAIM_TOKENS.search(r.get('gap', '')))
    summary['evaluator'] = EVALUATOR
    return summary


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
    p.add_argument('--split', choices=['tuning', 'holdout', 'holdout2', 'holdout3'], required=True)
    p.add_argument('--config', action='append', default=[], help='name=VAR=value;VAR=value')
    p.add_argument('--report', required=True)
    p.add_argument('--only', help='comma-separated question IDs')
    p.add_argument('--retrieval-only', action='store_true', help='ranking metrics only, no answers (fast A/B of search)')
    p.add_argument('--quick', action='store_true',
                   help='every 3rd question of each type (about a third of the set, all types kept) for fast iterations')
    p.add_argument('--rescore', help='score the pages saved in this report again (current evaluator and labels), no generator')
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
    old = None
    if args.rescore:
        old = json.loads(Path(args.rescore).read_text(encoding='utf-8'))
        if old['split'] != args.split:
            raise SystemExit(f'{args.rescore} is a report on {old["split"]}, not {args.split}')
        configs = {name: entry['flags'] for name, entry in old['configs'].items()}
        saved_ids = {a['id'] for entry in old['configs'].values() for a in entry['answers'] if a.get('page')}
        cases = [c for c in cases if c['id'] in saved_ids]
        if not cases:
            raise SystemExit('no saved pages in ' + args.rescore + ' (reports before eval3 keep only a text excerpt)')
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
        report = {'split': args.split, 'quick': args.quick, 'questions': [c['id'] for c in cases], 'data': 'synthetic',
                  'evaluator': EVALUATOR, 'configs': {}}
        if old is not None:
            report['rescored_from'] = args.rescore
            with closing(connect(str(db_path))) as db:
                for name, entry in old['configs'].items():
                    pages = {a['id']: a for a in entry['answers'] if a.get('page')}
                    results = [score(c, '</form>' + pages[c['id']]['page'], pages[c['id']]['seconds'], db, needles)
                               for c in cases if c['id'] in pages]
                    summary = summarize(cases, results)
                    report['configs'][name] = {'flags': entry['flags'], 'ranking': entry['ranking'], 'summary': summary, 'answers': results}
                    print(name, json.dumps(summary, ensure_ascii=False))
            configs = {}
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
