"""Re-check «false answer because of a forbidden pattern» in saved measure_rag reports
with the current rule (forbidden statements are searched outside the «no data for part
of the question» line). Prints the questions whose verdict changes; reports stay as they are.

  python eval/rescore_forbidden.py REPORT.json[:config] [questions.json]
"""
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from measure_rag import FIXTURES, fold, without_gap  # noqa: E402


def main():
    path, _, config = sys.argv[1].partition(':')
    report = json.loads(Path(path).read_text(encoding='utf-8'))
    cases = {c['id']: c for c in json.loads((FIXTURES / (report['split'] + '.json')).read_text(encoding='utf-8'))}
    for name, entry in report['configs'].items():
        if config and name != config:
            continue
        for a in entry['answers']:
            if a['verdict'] != 'false_answer' or not a['forbidden_found']:
                continue
            still = [f for f in cases[a['id']]['forbidden'] if re.search(fold(f), fold(without_gap(a['answer'])), re.I)]
            if not still:
                print(f'{name} {a["id"]}: forbidden only in the gap line -> not a false answer by this rule')


if __name__ == '__main__':
    main()
