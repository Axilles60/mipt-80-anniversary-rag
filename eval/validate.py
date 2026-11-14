import json
from pathlib import Path
import re

path = Path(__file__).with_name('synthetic.json')
data = json.loads(path.read_text(encoding='utf-8'))
assert isinstance(data, list) and data, 'Expected a nonempty list'
seen = set()
for case in data:
    assert set(case) == {'id', 'topic', 'question', 'conditions', 'required_facts', 'forbidden_conclusions', 'expected_fragments', 'status'}
    assert re.fullmatch(r'AC-SYN-\d{2}', case['id']) and case['id'] not in seen
    seen.add(case['id'])
    assert case['status'] == 'synthetic'
    for key in ('topic', 'question'):
        assert isinstance(case[key], str) and case[key].strip(), (case['id'], key)
    for key in ('conditions', 'required_facts', 'forbidden_conclusions', 'expected_fragments'):
        assert isinstance(case[key], list) and all(isinstance(x, str) and x.strip() for x in case[key]), (case['id'], key)
    assert case['required_facts'] and case['forbidden_conclusions']
    assert all(re.fullmatch(r'SYN-\d{2}#p\d+', x) for x in case['expected_fragments'])
assert len({c['topic'] for c in data if int(c['id'][-2:]) <= 11}) == 11
print(f'Validated {len(data)} synthetic cases; 11 topics')
