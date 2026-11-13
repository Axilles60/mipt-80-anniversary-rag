"""Document requisites and version relations read from the documents' own text.

No curated catalogue is needed: at query time (cached in the same SQLite file)
each imported version is scanned for
* its requisites — kind, number, signing date, entry into force
  ("вводится в действие с …", "с даты подписания");
* references to other acts it repeals ("признать утратившим силу",
  "отменить") or amends ("внести изменения", "изложить в новой редакции").
References are resolved to other imported versions by number (and date when
both are known). Answers then do not use a version that, on the question's
date (or today), is repealed by another imported act already in force, nor a
version not yet in force on that date; each decision is shown with a link to
the sentence it rests on (EVD-01/02/04, CASE-T04).

Limits: only what the texts state; an act absent from the corpus cannot be
resolved, and a partial repeal ("пункт 3 приказа …") marks the act as amended
rather than repealed. Source text is data, never instructions.
"""
from datetime import date
import json
import re

MONTHS = {'январ': 1, 'феврал': 2, 'март': 3, 'апрел': 4, 'мая': 5, 'май': 5, 'июн': 6, 'июл': 7,
          'август': 8, 'сентябр': 9, 'октябр': 10, 'ноябр': 11, 'декабр': 12}
_DATE = (r'(?:«?\s*(\d{1,2})\s*»?\s+(январ\w*|феврал\w*|март\w*|апрел\w*|ма[яй]|июн\w*|июл\w*|август\w*|'
         r'сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)\s+(\d{4})|(\d{1,2})\.(\d{1,2})\.(\d{4}))\s*(?:г(?:ода|\.)?)?')
DATE = re.compile(_DATE, re.I)
_NUMBER = r'№\s*(\d[\w\-–/]*\w|\d)'
# "от 12.02.2015 № 76-1", "№ 76-1 от 12.02.2015", "12.02.2015 № 76-1"
REF = re.compile(r'(?:от\s+)?' + _DATE + r'\s*' + _NUMBER + r'|' + _NUMBER + r'\s+от\s+' + _DATE, re.I)
KIND = re.compile(r'\b(приказ|распоряжени[ея]|постановлени[ея]|решени[ея]|положени[ея]|регламент|порядок|инструкци[яи])\b', re.I)
EFFECTIVE = re.compile(r'(?:вводит\w*|вступа\w*|действу\w*|применя\w*)\s+(?:в\s+(?:действие|силу)\s+)?с\s+' + _DATE, re.I)
FROM_SIGNING = re.compile(r'(?:вводит\w*|вступа\w*|действу\w*|применя\w*)\s+(?:в\s+(?:действие|силу)\s+)?'
                          r'с\s+(?:даты|момента|дня)\s+(?:его\s+)?(?:подписани|утверждени|регистраци)\w*', re.I)
REPEAL = re.compile(r'утратившим\w*\s+силу|утратил\w*\s+силу|\bотменить\b|\bотменяется\b|\bотменяются\b', re.I)
AMEND = re.compile(r'внести\s+(?:следующие\s+)?изменени\w*|изложить\s+в\s+(?:новой|следующей)\s+редакции|'
                   r'изменени\w*\s+в\s+(?:приказ|распоряжени|регламент|положени|порядок)\w*|новая\s+редакция', re.I)
PART = re.compile(r'\b(?:пункт\w*|п\.|подпункт\w*|абзац\w*|раздел\w*|приложени\w*|глав\w*)\s', re.I)
SCHEMA = 'CREATE TABLE IF NOT EXISTS doc_requisites (source_id TEXT, version TEXT, data TEXT, PRIMARY KEY(source_id, version))'
HEAD_CHARS = 700


def _iso(m, offset=0):
    """ISO date from a DATE/REF match group block starting at ``offset``."""
    g = m.groups()[offset:offset + 6]
    try:
        if g[0]:
            month = next(v for k, v in MONTHS.items() if g[1].lower().startswith(k))
            return date(int(g[2]), month, int(g[0])).isoformat()
        if g[3]:
            return date(int(g[5]), int(g[4]), int(g[3])).isoformat()
    except (ValueError, StopIteration):
        return None
    return None


def _number(value):
    return re.sub(r'\s+', '', value).replace('–', '-').rstrip('.').lower() if value else None


def references(text):
    """[(number, iso date or None, span)] of acts referenced in a text."""
    out = []
    for m in REF.finditer(text):
        if m.group(7):                       # date then number
            out.append((_number(m.group(7)), _iso(m, 0), m.span()))
        else:                                # number then date
            out.append((_number(m.group(8)), _iso(m, 8), m.span()))
    return out


def _sentences(text):
    return [s for s in re.split(r'(?<=[.;!?])\s+(?=[А-ЯЁA-Z0-9«])|\n{2,}', text) if s.strip()]


def scan(rows):
    """Requisites and relations of one version from its fragments (in order)."""
    texts = [(r['id'], r['text'] or '') for r in rows if (r['text'] or '').strip()]
    head, head_id, used = '', None, 0
    for fid, text in texts:
        if used >= HEAD_CHARS: break
        head_id = head_id or fid
        head += text[:HEAD_CHARS - used] + '\n'; used += len(text)
    data = {'kind': None, 'number': None, 'signed': None, 'effective': None, 'basis': head_id, 'relations': []}
    kind = KIND.search(head[:300])
    if kind: data['kind'] = kind.group(1).lower()
    own = references(head)
    if own:
        data['number'], data['signed'], _ = own[0]
    for fid, text in texts:
        if data['effective'] is None:
            m = EFFECTIVE.search(text)
            if m:
                data['effective'] = _iso(m, 0); data['effective_basis'] = fid
            elif FROM_SIGNING.search(text) and data['signed']:
                data['effective'] = data['signed']; data['effective_basis'] = fid
        for sentence in _sentences(text):
            repeal, amend = REPEAL.search(sentence), AMEND.search(sentence)
            if not (repeal or amend):
                continue
            for number, when, span in references(sentence):
                if number == data['number'] and (when is None or when == data['signed']):
                    continue  # its own requisites
                before = sentence[:span[0]]
                partial = bool(PART.search(before[-120:]))
                kind = 'repeals' if repeal and not partial else 'amends'
                data['relations'].append({'kind': kind, 'number': number, 'date': when, 'basis': fid,
                                          'text': sentence.strip()[:300]})
    return data


def requisites(db, source_id, version):
    try:
        db.execute(SCHEMA)
        row = db.execute('SELECT data FROM doc_requisites WHERE source_id=? AND version=?', (source_id, version)).fetchone()
    except Exception:
        row = None
    if row:
        return json.loads(row['data'])
    rows = db.execute('SELECT id, text FROM import_fragments WHERE source_id=? AND version=? ORDER BY rowid',
                      (source_id, version)).fetchall()
    data = scan(rows)
    try:
        with db:
            db.execute('INSERT OR REPLACE INTO doc_requisites VALUES (?,?,?)', (source_id, version, json.dumps(data, ensure_ascii=False)))
    except Exception:
        pass  # read-only database: keep the result for this request only
    return data


def label(data):
    """Human-readable requisites as stated in the text, or None."""
    if not data or not (data.get('number') or data.get('signed')):
        return None
    out = (data.get('kind') or 'документ').capitalize()
    if data.get('number'): out += ' № ' + data['number']
    if data.get('signed'): out += ' от ' + _ru(data['signed'])
    if data.get('effective') and data['effective'] != data.get('signed'):
        out += ', вводится с ' + _ru(data['effective'])
    return out


def _ru(iso):
    y, m, d = iso.split('-'); return f'{d}.{m}.{y}'


def _start(data):
    return data.get('effective') or data.get('signed')


def status(db, versions, when=None):
    """Per version: ('ok'|'repealed'|'not_yet'|'uncertain', [(note, basis fragment)]).

    ``versions``: iterable of (source_id, version). Relations are looked up
    among all imported versions, so an act repealing a cited one counts even
    if it was not retrieved itself."""
    all_versions = [(r['source_id'], r['version']) for r in db.execute('SELECT source_id, version FROM imports')]
    info = {key: requisites(db, *key) for key in all_versions}
    today = date.today().isoformat()
    moment = when or today
    out = {}
    for key in dict.fromkeys(versions):
        data = info.get(key) or requisites(db, *key)
        notes, state = [], 'ok'
        start = _start(data)
        if when and start and when < start:
            state = 'not_yet'
            notes.append((f'{label(data)}: на {_ru(when)} ещё не действовал (по тексту документа); '
                          'для этой даты он не используется.', data.get('effective_basis') or data.get('basis')))
        for other, odata in info.items():
            if other == key: continue
            for rel in odata['relations']:
                if not data.get('number') or rel['number'] != data['number']: continue
                if rel['date'] and data.get('signed') and rel['date'] != data['signed']: continue
                ostart = _start(odata)
                if rel['kind'] == 'repeals':
                    if when and ostart and ostart > when:
                        # Historical question: this version applies, a later one exists.
                        notes.append((f'На {_ru(when)} действовал {label(data)}; с {_ru(ostart)} он признан утратившим '
                                      f'силу документом {label(odata)}.', rel['basis']))
                    elif ostart and ostart <= moment:
                        state = 'repealed' if state == 'ok' else state
                        notes.append((f'{label(data)} признан утратившим силу документом {label(odata)} '
                                      f'(с {_ru(ostart)}); его пункты в ответ не включены.', rel['basis']))
                    elif ostart is None:
                        state = 'uncertain' if state == 'ok' else state
                        notes.append((f'{label(data)} отменяется документом без установленной даты действия; '
                                      'применимость на выбранную дату не подтверждена.', rel['basis']))
                elif ostart is None or ostart <= moment:
                    notes.append((f'В {label(data)} внесены изменения документом {label(odata) or "из базы"}; '
                                  'сверьте изменённые пункты.', rel['basis']))
        # The version itself repeals an imported act: say so, since that act's
        # points may have been dropped before they could be cited.
        if state in ('ok', 'uncertain') and (not start or start <= moment):
            for rel in data['relations']:
                if rel['kind'] != 'repeals': continue
                for other, odata in info.items():
                    if other == key or odata.get('number') != rel['number']: continue
                    if rel['date'] and odata.get('signed') and rel['date'] != odata['signed']: continue
                    note = (f'{label(data)} признаёт утратившим силу {label(odata)}; '
                            'пункты отменённого документа в ответ не включены.', rel['basis'])
                    if note not in notes: notes.append(note)
        out[key] = (state, notes)
    return out


def applicable(db, rows, when=None):
    """Rows of versions usable on ``when`` and the notes explaining exclusions."""
    keys = [(r['source_id'], r['version']) for r in rows]
    states = status(db, keys, when)
    kept = [r for r in rows if states[(r['source_id'], r['version'])][0] in ('ok', 'uncertain')]
    notes = []
    for key in dict.fromkeys(keys):
        for note in states[key][1]:
            if note not in notes: notes.append(note)
    return kept, notes
