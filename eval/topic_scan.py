"""The «outside the base» classifier alone (rag.topic), on every gold question.

  python eval/topic_scan.py [--sets tuning,holdout2,holdout3]

Needs the generator (NAVIGATOR_LLM_URL or the local model). In the pipeline the
classifier runs only when retrieval returns nothing usable, so each of its errors
on a work question becomes «вне темы» instead of «недостаточно данных» or an
answer. Here it is asked about every question, so its own error rate is visible:
work questions classified off_topic and off-topic questions classified work.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import rag  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sets', default='tuning,holdout,holdout2,holdout3')
    args = p.parse_args()
    model = rag.llm()
    if model is None:
        raise SystemExit('generator unavailable')
    total = Counter()
    for name in args.sets.split(','):
        cases = json.loads((ROOT / 'fixtures' / 'rag' / f'{name}.json').read_text(encoding='utf-8'))
        for c in cases:
            want = 'off_topic' if c['expect'] == 'off_topic' else 'work'
            got = 'off_topic' if rag.topic(model, c['question']) == 'off_topic' else 'work'
            total[want, got] += 1
            if want != got:
                print(f'{name:9} {c["id"]:5} want {want:9} got {got:9} [{c["type"]}] {c["question"]}')
    print('work → off_topic:', total['work', 'off_topic'], 'of', total['work', 'work'] + total['work', 'off_topic'])
    print('off_topic → work:', total['off_topic', 'work'], 'of', total['off_topic', 'off_topic'] + total['off_topic', 'work'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
