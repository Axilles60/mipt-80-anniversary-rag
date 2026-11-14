"""Curated evidence catalogue. Source text is data, never instructions."""
import json
import re
from datetime import date

SCHEMA = '''
CREATE TABLE IF NOT EXISTS evidence_documents (id TEXT PRIMARY KEY, title TEXT NOT NULL, signed TEXT, effective TEXT, valid_from TEXT, valid_to TEXT, approval TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence_fragments (id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES evidence_documents(id), locator TEXT NOT NULL, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence_rules (id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES evidence_documents(id), fragment_id TEXT NOT NULL REFERENCES evidence_fragments(id), topic TEXT NOT NULL, kind TEXT NOT NULL, start TEXT, end TEXT, conditions TEXT NOT NULL, exceptions TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence_relations (id TEXT PRIMARY KEY, kind TEXT NOT NULL, origin TEXT NOT NULL, target TEXT NOT NULL, basis TEXT NOT NULL REFERENCES evidence_fragments(id));
CREATE TABLE IF NOT EXISTS evidence_limits (id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES evidence_documents(id), topic TEXT NOT NULL, description TEXT NOT NULL, basis TEXT NOT NULL REFERENCES evidence_fragments(id));
CREATE TABLE IF NOT EXISTS evidence_topics (id TEXT PRIMARY KEY, keywords TEXT NOT NULL);
'''

def iso(value):
    if value is not None:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError('Expected ISO date YYYY-MM-DD')
    return value

def load(db, catalog):
    """Replace a curated catalogue atomically, rejecting unresolved references."""
    db.executescript(SCHEMA)
    docs = {x['id']: x for x in catalog['documents']}
    fragments = {x['id']: x for x in catalog['fragments']}
    rules = {x['id']: x for x in catalog['rules']}
    topics = {x['id']: x for x in catalog['topics']}
    for name, values, index in [('documents',catalog['documents'],docs),('fragments',catalog['fragments'],fragments),('rules',catalog['rules'],rules),('topics',catalog['topics'],topics)]:
        if len(values) != len(index): raise ValueError('Duplicate IDs: '+name)
    for d in docs.values():
        for key in ('signed','effective','valid_from','valid_to'): iso(d.get(key))
        if d['approval'] not in ('confirmed','incomplete','unknown'): raise ValueError('Invalid approval status')
    for f in fragments.values():
        if f['document_id'] not in docs or not f['text'] or not f['locator']: raise ValueError('Invalid fragment')
    for r in rules.values():
        if r['fragment_id'] not in fragments or fragments[r['fragment_id']]['document_id'] != r['document_id'] or r['topic'] not in topics: raise ValueError('Invalid rule references')
        if r['kind'] not in ('norm','form','example','instruction','historical_task'): raise ValueError('Invalid rule kind')
        for k in ('start','end'): iso(r.get(k))
        if not isinstance(r.get('conditions',{}),dict) or not isinstance(r.get('exceptions',{}),dict): raise ValueError('Invalid conditions')
    for rel in catalog.get('relations',[]):
        if rel['kind'] not in ('amends_rule','repeals_rule','amends_document','repeals_document','attachment','duplicate','conflict','gap') or rel['basis'] not in fragments: raise ValueError('Invalid relation')
        pool = rules if rel['kind'] in ('amends_rule','repeals_rule','conflict') else docs
        if rel['origin'] not in pool or rel['target'] not in pool: raise ValueError('Unresolved relation')
    for limit in catalog.get('limits',[]):
        if limit['basis'] not in fragments or limit['document_id'] not in docs or limit['topic'] not in topics: raise ValueError('Invalid limitation')
    with db:
        for table in ('evidence_relations','evidence_limits','evidence_rules','evidence_fragments','evidence_documents','evidence_topics'):
            db.execute('DELETE FROM '+table)
        for d in docs.values(): db.execute('INSERT INTO evidence_documents VALUES (?,?,?,?,?,?,?)', (d['id'],d['title'],d.get('signed'),d.get('effective'),d.get('valid_from'),d.get('valid_to'),d['approval']))
        for f in fragments.values(): db.execute('INSERT INTO evidence_fragments VALUES (?,?,?,?)',(f['id'],f['document_id'],f['locator'],f['text']))
        for t in topics.values(): db.execute('INSERT INTO evidence_topics VALUES (?,?)',(t['id'],json.dumps(t['keywords'],ensure_ascii=False)))
        for r in rules.values(): db.execute('INSERT INTO evidence_rules VALUES (?,?,?,?,?,?,?,?,?)',(r['id'],r['document_id'],r['fragment_id'],r['topic'],r['kind'],r.get('start'),r.get('end'),json.dumps(r.get('conditions',{}),ensure_ascii=False),json.dumps(r.get('exceptions',{}),ensure_ascii=False)))
        for rel in catalog.get('relations',[]): db.execute('INSERT INTO evidence_relations VALUES (?,?,?,?,?)',(rel['id'],rel['kind'],rel['origin'],rel['target'],rel['basis']))
        for l in catalog.get('limits',[]): db.execute('INSERT INTO evidence_limits VALUES (?,?,?,?,?)',(l['id'],l['document_id'],l['topic'],l['description'],l['basis']))

def keyword_matches(keyword, question):
    """Match complete words with limited Russian case endings, not substrings."""
    def words(text):
        return re.findall(r'[а-яa-z0-9]+', text.lower().replace('ё', 'е'))
    key, query = words(keyword), words(question)
    endings = {'', 'а', 'у', 'е', 'ы', 'и', 'ой', 'ою', 'ом', 'ам', 'ах', 'ами', 'я', 'ю', 'ей', 'ям', 'ях', 'ями'}
    def matches(expected, actual):
        if expected == actual: return True
        if len(expected) < 4: return False
        if actual.startswith(expected) and actual[len(expected):] in endings: return True
        return expected[-1:] in ('а', 'я') and actual.startswith(expected[:-1]) and actual[len(expected)-1:] in endings
    return bool(key) and any(all(matches(k, q) for k, q in zip(key, query[i:i+len(key)]))
                             for i in range(len(query)-len(key)+1))

def query(db, question, when=None, conditions=None, imported_only=False):
    """Return source-linked evidence, uncertainties and clarification requests."""
    conditions = conditions or {}
    iso(when)
    topics = [r['id'] for r in db.execute('SELECT * FROM evidence_topics') if any(keyword_matches(k, question) for k in json.loads(r['keywords']))]
    if imported_only:
        # A rule is usable only when the curated evidence resolves exactly to
        # an actual imported fragment, including text and locator.
        valid = set()
        for fragment in db.execute('''SELECT f.id,i.metadata FROM evidence_fragments f
                 JOIN import_fragments i ON i.id=f.id AND i.text=f.text AND i.locator=f.locator'''):
            metadata = json.loads(fragment['metadata'])
            if metadata.get('requires_visual_review') or metadata.get('formula_error') or metadata.get('ocr_needed'):
                continue
            valid.add(fragment['id'])
        topics = [t for t in topics if any(r['fragment_id'] in valid for r in db.execute(
            'SELECT fragment_id FROM evidence_rules WHERE topic=?', (t,)))]

    else:
        valid = None
    if not topics: return None
    result = {'items':[], 'limits':[], 'questions':[], 'conflicts':[]}
    if not when:
        result['questions'].append('Укажите дату, на которую нужен порядок.')
        return result
    candidates = []
    undated = []
    for topic in topics:
        for row in db.execute('''SELECT r.*,d.title,d.approval,d.effective,d.valid_from,d.valid_to,f.text FROM evidence_rules r JOIN evidence_documents d ON d.id=r.document_id JOIN evidence_fragments f ON f.id=r.fragment_id WHERE r.topic=?''',(topic,)):
            if row['kind'] != 'norm' or (valid is not None and row['fragment_id'] not in valid): continue
            start, end = row['start'] or row['valid_from'] or row['effective'], row['end'] or row['valid_to']
            if not start:
                result['limits'].append({'text':'Дата начала действия не подтверждена.', 'basis':row['fragment_id']})
                undated.append(row)
                continue
            if when < start or (end and when > end): continue
            required = json.loads(row['conditions']); excluded = json.loads(row['exceptions'])
            if any(conditions.get(key) and conditions[key] != val for key,val in required.items()): continue
            if any(conditions.get(key) and conditions[key] == val for key,val in excluded.items()): continue
            missing = [key for key in set(required)|set(excluded) if not conditions.get(key)]
            if missing:
                for key in missing:
                    msg = 'Уточните условие: '+key+'.'
                    if msg not in result['questions']: result['questions'].append(msg)
                continue
            candidates.append(row)
        for l in db.execute('SELECT l.*, d.effective, d.valid_from, d.valid_to FROM evidence_limits l JOIN evidence_documents d ON d.id=l.document_id WHERE l.topic=?',(topic,)):
            limit_start = l['valid_from'] or l['effective']
            if (limit_start and when < limit_start) or (l['valid_to'] and when > l['valid_to']): continue
            if valid is None or l['basis'] in valid:
                result['limits'].append({'text':l['description'],'basis':l['basis']})
    # An explicitly recorded gap is itself evidence of uncertainty, even if
    # the curator did not duplicate it as a separate limitation.
    for rel in db.execute("SELECT * FROM evidence_relations WHERE kind='gap'"):
        if valid is not None and rel['basis'] not in valid:
            continue
        origin = db.execute('SELECT effective,valid_from,valid_to FROM evidence_documents WHERE id=?', (rel['origin'],)).fetchone()
        start = origin['valid_from'] or origin['effective']
        if (start and when < start) or (origin['valid_to'] and when > origin['valid_to']):
            continue
        if db.execute('SELECT 1 FROM evidence_rules WHERE document_id IN (?,?) AND topic IN (' + ','.join('?' for _ in topics) + ') LIMIT 1', (rel['origin'], rel['target'], *topics)).fetchone():
            result['limits'].append({'text':'Цепочка редакций неполна; промежуточные изменения не подтверждены.', 'basis':rel['basis']})
    # Amendments only override the explicitly identified rule, not the whole document.
    active_ids = {r['id'] for r in candidates}
    superseded = set()
    for rel in db.execute("SELECT * FROM evidence_relations WHERE kind IN ('amends_rule','repeals_rule','conflict')"):
        if (valid is None or rel['basis'] in valid) and rel['origin'] in active_ids and rel['target'] in active_ids:
            if rel['kind'] == 'conflict': result['conflicts'].append({'rules':[rel['origin'],rel['target']], 'basis':rel['basis']})
            else: superseded.add(rel['target'])
    for rel in db.execute("SELECT * FROM evidence_relations WHERE kind IN ('amends_document','repeals_document')"):
        active_documents = {r['document_id'] for r in candidates}
        if (valid is None or rel['basis'] in valid) and rel['origin'] in active_documents:
            for row in candidates:
                if row['document_id'] == rel['target']:
                    superseded.add(row['id'])
            if rel['target'] in active_documents:
                result['limits'].append({'text':'Документ изменён или отменён на основании связанного документа.', 'basis':rel['basis']})
    for rel in db.execute("SELECT * FROM evidence_relations WHERE kind IN ('amends_rule','repeals_rule')"):
        if (valid is None or rel['basis'] in valid) and rel['origin'] in active_ids and rel['target'] in active_ids:
            result['limits'].append({'text':'Отдельный пункт изменён или отменён.', 'basis':rel['basis']})
    # A shared import source is only a storage identity, not proof that one
    # version supersedes another. Do not present overlapping unlinked versions
    # or a possibly relevant undated version as an established applicable rule.
    uncertain = set()
    if imported_only:
        identities = {r['id']: (r['source_id'], r['version']) for r in db.execute(
            'SELECT id, source_id, version FROM import_fragments')}
        relations = list(db.execute("SELECT * FROM evidence_relations WHERE kind IN "
                                    "('amends_rule','repeals_rule','amends_document','repeals_document','conflict')"))
        for index, first in enumerate(candidates):
            a = identities.get(first['fragment_id'])
            for second in candidates[index + 1:] + undated:
                b = identities.get(second['fragment_id'])
                if not a or not b or a[0] != b[0] or a[1] == b[1] or first['topic'] != second['topic']:
                    continue
                linked = any((valid is None or rel['basis'] in valid) and
                             ((rel['origin'], rel['target']) in ((first['id'], second['id']), (second['id'], first['id']))
                              if rel['kind'] in ('amends_rule', 'repeals_rule', 'conflict') else
                              (rel['origin'], rel['target']) in ((first['document_id'], second['document_id']), (second['document_id'], first['document_id'])))
                             for rel in relations)
                # A confirmed rule-level relation covers only its named rule;
                # it must not turn other points of the older document into
                # replacements of that same rule.
                explicitly_scoped = False
                if not linked:
                    for rel in relations:
                        if rel['kind'] not in ('amends_rule', 'repeals_rule') or (valid is not None and rel['basis'] not in valid):
                            continue
                        for row, other in ((first, second), (second, first)):
                            if row['id'] not in (rel['origin'], rel['target']):
                                continue
                            related_id = rel['target'] if rel['origin'] == row['id'] else rel['origin']
                            related = db.execute('SELECT document_id FROM evidence_rules WHERE id=?', (related_id,)).fetchone()
                            if related and related['document_id'] == other['document_id']:
                                explicitly_scoped = True
                if (second in undated or not linked) and not explicitly_scoped:
                    uncertain.add(first['id'])
                    if second in candidates: uncertain.add(second['id'])
                    message = ('Дата действия одной из редакций не подтверждена; применимая редакция не установлена.'
                               if second in undated else
                               'Связь редакций не подтверждена; применимая редакция не установлена.')
                    for row in (first, second):
                        item = {'text': message, 'basis': row['fragment_id']}
                        if item not in result['limits']: result['limits'].append(item)
    from .sufficiency import supports
    from .procedures import staged
    # A composite request can be supported by several distinct fragments.
    # Deadlines are assessed as a separate facet, not required in every step.
    check_question = re.sub(r'\bсрок\w*', '', question, flags=re.I) if staged(question) else question
    unsupported = False
    for row in candidates:
        if row['id'] in superseded or row['id'] in uncertain: continue
        if not supports(check_question, row['text']):
            unsupported = True
            continue
        result['items'].append({'id':row['id'],'text':row['text'],'basis':row['fragment_id'],'title':row['title']})
        if row['approval'] != 'confirmed': result['limits'].append({'text':'Реквизиты утверждения не подтверждены; порядок изложен условно.','basis':row['fragment_id']})
    if unsupported and not result['items'] and not result['questions'] and not uncertain:
        # Topic and version alone do not substantiate the requested action.
        result['limits'] = []
        result['conflicts'] = []
    if not result['items'] and not result['questions']:
        result['limits'].append({'text':'Недостаточно подтверждённых данных для выбранных условий.', 'basis':None})
    return result
