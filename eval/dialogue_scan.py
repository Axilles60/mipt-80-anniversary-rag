"""How the dialogue is understood, measured apart from retrieval and answers.

  python eval/dialogue_scan.py            # needs the generator (NAVIGATOR_LLM_URL or the local model)

For every case of fixtures/rag/dialogues.json: the relation of the new question to
the earlier turns (history.relation: «new» / «follow», by code), and for a follow-up
the question as rewritten (rag.condense). A case is right when the relation is the
expected one and the question that goes to retrieval (rewritten or as asked) matches
every «must» pattern and no «must_not» pattern — numbers from earlier answers must not
come into it. Prints the failures and the shares.
"""
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import history, rag  # noqa: E402


def main():
    cases = json.loads((ROOT / 'fixtures' / 'rag' / 'dialogues.json').read_text(encoding='utf-8'))
    model = rag.llm()
    if model is None:
        raise SystemExit('generator unavailable')
    right_relation = right = 0
    for c in cases:
        turns = [dict(t, standalone=t.get('standalone') or t['question']) for t in c['history']]
        relation = history.relation(c['question'], turns)
        asked = rag.condense(model, turns, c['question']) if relation == 'follow' else c['question']
        ok_relation = relation == c['relation']
        missing = [p for p in c['must'] if not re.search(p, asked, re.I)]
        extra = [p for p in c['must_not'] if re.search(p, asked, re.I)]
        ok = ok_relation and not missing and not extra
        right_relation += ok_relation; right += ok
        mark = 'ok ' if ok else 'BAD'
        print(f'{mark} {c["id"]} [{relation}{"" if ok_relation else " ≠ " + c["relation"]}] {c["question"]!r} → {asked!r}'
              + (f' | нет: {missing}' if missing else '') + (f' | лишнее: {extra}' if extra else ''))
    print(f'relation right: {right_relation}/{len(cases)}; understood right: {right}/{len(cases)}')


if __name__ == '__main__':
    main()
