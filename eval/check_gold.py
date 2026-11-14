"""Check the labelling of the gold question sets fixtures/rag/{tuning,holdout,holdout2}.json.

  python eval/check_gold.py          # prints counts by type and problems; exit 1 if any

Checks, by code only:
- unique question IDs; no repeated question (same text and date) across both sets;
- every expected key exists in fixtures/rag/corpus.json;
- answer/partial questions have expected units and facts, and every fact pattern
  occurs in the text of the expected units (except calculated facts, "derived");
- insufficient/off_topic questions have no expected units and no facts;
- regular expressions compile;
- a unit's needle does not occur in another unit's text (otherwise a citation of
  that unit would be counted as the wrong one), except known cases listed below.
"""
from collections import Counter
import json
from pathlib import Path
import re
import sys

FIX = Path(__file__).resolve().parents[1] / 'fixtures' / 'rag'
SETS = ('tuning', 'holdout', 'holdout2')
EXPECTS = {'answer', 'partial', 'insufficient', 'off_topic'}
# The repealing order quotes the repealed order's requisites verbatim.
KNOWN_OVERLAPS = {('PO-REQ', 'PN-2')}


def norm(text):
    return re.sub(r'[\s­]+', ' ', text.lower().replace('ё', 'е')).strip()


def corpus_units():
    """{key: text as a reader sees it} and {key: normalised needle}."""
    corpus = json.loads((FIX / 'corpus.json').read_text(encoding='utf-8'))
    units, needles = {}, {}
    for doc in corpus['documents']:
        for p in doc.get('paragraphs') or [p for page in doc.get('pages', []) for p in page]:
            units[p['key']] = p['text']; needles[p['key']] = norm(p.get('needle', p['text']))
        rows = doc.get('rows') or []
        for row in rows[1:]:
            keyed = [c for c in row if isinstance(c, dict)]
            if keyed:
                values = [c['needle'] if isinstance(c, dict) else str(c) for c in row]
                units[keyed[0]['key']] = '; '.join(f'{h}: {v}' for h, v in zip(rows[0], values))
                needles[keyed[0]['key']] = norm(keyed[0]['needle'])
    return units, needles


def check():
    units, needles = corpus_units()
    sets = {s: json.loads((FIX / f'{s}.json').read_text(encoding='utf-8')) for s in SETS if (FIX / f'{s}.json').exists()}
    problems = [f'needle of {k} occurs in {o}' for k, n in needles.items() for o, t in units.items()
                if o != k and n in norm(t) and (k, o) not in KNOWN_OVERLAPS]
    every = [x for rows in sets.values() for x in rows]
    problems += [f'duplicate id {i}' for i, c in Counter(x['id'] for x in every).items() if c > 1]
    problems += [f'repeated question: {q!r} {d}' for (q, d), c in
                 Counter((norm(x['question']), x.get('date', '')) for x in every).items() if c > 1]
    for x in every:
        where = x['id']
        if x.get('expect') not in EXPECTS:
            problems.append(f'{where}: expect {x.get("expect")!r}'); continue
        problems += [f'{where}: unknown key {k}' for k in x['expected'] if k not in units]
        for f in x['facts'] + x['forbidden']:
            try: re.compile(f)
            except re.error as e: problems.append(f'{where}: bad pattern {f!r}: {e}')
        if x['expect'] in ('insufficient', 'off_topic'):
            if x['expected'] or x['facts']: problems.append(f'{where}: refusal with expected units or facts')
            continue
        if not x['expected'] or not x['facts']:
            problems.append(f'{where}: answer without expected units or facts'); continue
        if not x.get('derived'):
            text = ' '.join(units.get(k, '') for k in x['expected'])
            problems += [f'{where}: fact {f!r} not in expected units' for f in x['facts'] if not re.search(f, text, re.I)]
    return sets, problems


def main():
    sets, problems = check()
    for name, rows in sets.items():
        print(name, len(rows), dict(sorted(Counter(x['type'] for x in rows).items())))
        print('  expect', dict(sorted(Counter(x['expect'] for x in rows).items())))
    print('problems:', len(problems))
    for p in problems: print(' ', p)
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
