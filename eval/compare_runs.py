"""Paired comparison of two measure_rag runs on the same questions.

  python eval/compare_runs.py A.json[:config] B.json[:config]

For every question the verdicts of A and B are paired. Exact McNemar test
(two-sided binomial over the discordant pairs) for «false answer» and for
«correct»: an improvement of a few questions out of 176 is usually noise.
Lists the questions that changed, so that each can be read by hand.

Runs are comparable only on the same labels and evaluator. A question whose
gold record differs (hash «gold» in eval3 reports; question text and expected
status in older ones) is left out and listed; different evaluator versions or
question sets are reported before the result. McNemar treats questions as
independent: minimal pairs and paraphrases of one rule are not.
"""
from collections import Counter
import json
from math import comb
from pathlib import Path
import sys


def load(spec):
    # «report.json» alone means its first configuration.
    path, _, config = (spec, '', '') if spec.endswith('.json') else spec.rpartition(':')
    report = json.loads(Path(path).read_text(encoding='utf-8'))
    config = config or next(iter(report['configs']))
    return report.get('evaluator', 'eval2 or older'), {a['id']: a for a in report['configs'][config]['answers']}


def mcnemar(b, c):
    """Two-sided exact p-value for b vs c discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)


def same_gold(x, y):
    if 'gold' in x and 'gold' in y:
        return x['gold'] == y['gold']
    return x['question'] == y['question'] and x['expect'] == y['expect']


def pairs(a, b):
    """Comparable IDs, IDs in one run only, IDs whose labels differ."""
    common = [i for i in a if i in b]
    differ = [i for i in common if not same_gold(a[i], b[i])]
    only = sorted(set(a) ^ set(b))
    return [i for i in common if i not in differ], only, differ


def main():
    (ea, a), (eb, b) = load(sys.argv[1]), load(sys.argv[2])
    ids, only, differ = pairs(a, b)
    if ea != eb:
        print(f'WARNING: different evaluators ({ea} vs {eb}): verdicts are not comparable as such')
    if only:
        print(f'WARNING: {len(only)} question(s) in one run only, left out: {", ".join(only[:20])}')
    if differ:
        print(f'WARNING: {len(differ)} question(s) with different labels, left out: {", ".join(differ)}')
    print(f'questions: {len(ids)}')
    for label, bad in (('false answer', lambda v: v == 'false_answer'), ('not correct', lambda v: v != 'correct')):
        fixed = [i for i in ids if bad(a[i]['verdict']) and not bad(b[i]['verdict'])]
        broke = [i for i in ids if not bad(a[i]['verdict']) and bad(b[i]['verdict'])]
        print(f'{label}: A {sum(bad(a[i]["verdict"]) for i in ids)} -> B {sum(bad(b[i]["verdict"]) for i in ids)}; '
              f'fixed {len(fixed)}, new {len(broke)}, exact McNemar p = {mcnemar(len(fixed), len(broke)):.3g}')
    print('transitions:', dict(Counter(f'{a[i]["verdict"]}->{b[i]["verdict"]}' for i in ids if a[i]['verdict'] != b[i]['verdict'])))
    for i in ids:
        if a[i]['verdict'] != b[i]['verdict']:
            print(f'  {i} {a[i]["type"]}: {a[i]["verdict"]} -> {b[i]["verdict"]} | {a[i]["question"][:70]}')


if __name__ == '__main__':
    main()
