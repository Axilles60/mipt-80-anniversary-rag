"""Locate the step at which each staged-procedure fact is lost (synthetic only).

Usage from the project root:
    python eval/diagnose_staged.py [--format pdf|docx] [--report PATH]

A fresh temporary database is created; fictional documents are imported with
the standard CLI and questions are sent to POST /ask on loopback. For every
expected fact the report states whether it was extracted, retrieved among the
search candidates, kept by ranking/selection, bound to the right stage and
shown in the answer next to a citation link of the right fragment.
"""
from contextlib import closing
from html.parser import HTMLParser
import argparse
import html
import re
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'eval'))
from synthetic_docs import TRIP_COMPETITORS, TRIP_FACTS, TRIP_PAGES, trip_docx_paragraphs, write_docx, write_pdf  # noqa: E402

QUESTIONS = [
    'Обычная служебная поездка: когда подать документы и что делать после возвращения?',
    'Какие формы и сроки нужны до и после командировки?',
    'Что оформить перед поездкой и что сдать после возвращения из командировки?',
]


class Items(HTMLParser):
    def __init__(self):
        super().__init__(); self.items = []; self.item = None; self.heading = None; self.in_h = False
    def handle_starttag(self, tag, attrs):
        if tag in ('h3', 'h4'): self.in_h = True; self.heading = ''
        if tag in ('li', 'p'): self.item = {'text': '', 'links': [], 'heading': self.heading}
        if tag == 'a' and self.item is not None:
            href = dict(attrs).get('href', '')
            if urlsplit(href).path == '/fragment':
                self.item['links'].append(parse_qs(urlsplit(href).query)['id'][0]); self.item.setdefault('hrefs', []).append(href)
    def handle_data(self, data):
        if self.in_h: self.heading += data
        if self.item is not None: self.item['text'] += data
    def handle_endtag(self, tag):
        if tag in ('h3', 'h4'): self.in_h = False
        if tag in ('li', 'p') and self.item is not None: self.items.append(self.item); self.item = None


def build(root, fmt, pages=TRIP_PAGES):
    paths = []
    if fmt == 'pdf':
        paths.append(write_pdf(root / 'trip.pdf', pages))
    else:
        paths.append(write_docx(root / 'trip.docx', trip_docx_paragraphs(pages)))
    for name, pages in TRIP_COMPETITORS.items():
        paths.append(write_pdf(root / name, pages))
    return [str(p) for p in paths]


def serve(env):
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); env['PORT'] = str(s.getsockname()[1])
    proc = subprocess.Popen([sys.executable, '-m', 'app'], env=env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = 'http://127.0.0.1:' + env['PORT']
    for _ in range(80):
        try: urlopen(base + '/', timeout=1).close(); return proc, base
        except OSError: time.sleep(.1)
    proc.terminate(); raise RuntimeError('server unavailable')


def diagnose(fmt='pdf', questions=QUESTIONS, pages=TRIP_PAGES, facts=TRIP_FACTS):
    from app.core import connect, imported_parts
    from app.retrieval import imported, coherent_candidates
    from app.questions import split_question
    from app import procedures
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); manifest = root / 'manifest.json'; manifest.write_text('[]')
        env = dict(os.environ, NAVIGATOR_DB=str(root / 'db'), HOST='127.0.0.1'); env.pop('NAVIGATOR_MODEL', None)
        subprocess.run([sys.executable, '-m', 'app', 'import', *build(root, fmt, pages), '--manifest', str(manifest)],
                       env=env, cwd=ROOT, check=True, capture_output=True)
        proc, base = serve(env)
        report = []
        try:
            with closing(connect(env['NAVIGATOR_DB'])) as db:
                fragments = [dict(r) for r in db.execute('SELECT * FROM import_fragments ORDER BY rowid')]
                for q in questions:
                    candidates = {r['id'] for r in imported(db, q, k=100, gate=False)}
                    coherent = coherent_candidates(db, q, split_question(q)) or []
                    selected, gaps, _ = imported_parts(db, q)
                    page = urlopen(base + '/ask', data=urlencode({'question': q, 'date': '2024-01-01'}).encode(), timeout=10).read().decode()
                    parsed = Items(); parsed.feed(page)
                    units = getattr(procedures, 'units', None)
                    rows_out = []
                    for stage, label, needle in facts:
                        holder = next((f for f in fragments if needle in f['text']), None)
                        row = {'stage': stage, 'facet': label, 'text': needle,
                               'extracted': holder is not None,
                               'fragment': holder['locator'] if holder else None}
                        if holder:
                            row['retrieved'] = holder['id'] in candidates
                            row['ranked'] = holder['id'] in {r['id'] for r in coherent} or holder['id'] in {r['id'] for r in selected}
                            if units:
                                bound = [u['stage'] for u in units(db, holder) if needle in u['text']]
                                row['stage_bound'] = bound[0] if bound else None
                            else:
                                row['stage_bound'] = procedures.stage(holder['text'])
                            shown = [i for i in parsed.items if needle in i['text'] and holder['id'] in i['links']]
                            row['shown_with_citation'] = bool(shown)
                            if shown:
                                source = urlopen(base + shown[0]['hrefs'][shown[0]['links'].index(holder['id'])], timeout=10).read().decode()
                                mark = re.search(r'<mark id="cited">(.*?)</mark>', re.sub(r'\s+', ' ', html.unescape(source)))
                                row['highlighted'] = bool(mark and needle in mark.group(1))
                            row['shown_under'] = shown[0]['heading'] if shown else None
                        loss = None
                        # Direct FTS retrieval of every fragment is informative but not required:
                        # once the version is chosen, its other units are read in document order.
                        for step in ('extracted', 'ranked'):
                            if not row.get(step): loss = step; break
                        if loss is None and row.get('stage_bound') != stage: loss = 'stage_binding'
                        if loss is None and not row.get('shown_with_citation'): loss = 'assembly'
                        if loss is None and not row.get('highlighted'): loss = 'source_view'
                        row['lost_at'] = loss
                        rows_out.append(row)
                    gaps_shown = [i['text'].strip() for i in parsed.items if 'Недостаточно данных' in i['text']]
                    report.append({'question': q, 'gaps_reported': gaps_shown, 'facts': rows_out,
                                   'lost': sum(1 for f in rows_out if f['lost_at']), 'html': page})
        finally:
            proc.terminate(); proc.wait(timeout=5)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--format', choices=['pdf', 'docx'], default='pdf')
    parser.add_argument('--report')
    args = parser.parse_args()
    questions = diagnose(args.format)
    for q in questions: q.pop('html')
    result = {'data': 'synthetic', 'format': args.format, 'questions': questions}
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.report:
        Path(args.report).write_text(text + '\n', encoding='utf-8')
    print(text)
