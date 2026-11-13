"""Conservative grouping of explicitly stated stages in extractive answers.

A fragment is a storage unit (a PDF page, a DOCX paragraph, a table row). A
page can hold several procedure stages, and a numbered item often relies on
its section heading for the stage. Stages are therefore assigned to smaller
units (numbered items or sentences) with the section heading as context.
Every unit keeps the ID of the fragment it was cut from, so a citation still
opens the imported fragment, its version and locator.
"""
import json
import re

# Question-level markers: any "до/перед ... после" request is a staged request.
BEFORE = re.compile(r'\b(?:до|перед)\s+', re.I)
AFTER = re.compile(r'\bпосле\s+', re.I)

# Text-level markers must name the event. A bare "до" is a range bound ("до
# пяти рабочих дней", "до 700 единиц") and does not identify a stage.
_EVENT = (r'(?:начал\w*|выезд\w*|отъезд\w*|отправлен\w*|поездк\w*|командир\w*|'
          r'служебн\w*|даты\s+(?:выезд|отъезд|начал|поездк)\w*|дня\s+(?:выезд|отъезд|поездк)\w*)')
BEFORE_TEXT = re.compile(r'\b(?:до|перед)\s+' + _EVENT + r'|\bзаблаговременно\b', re.I)
AFTER_TEXT = re.compile(r'\bпосле\s+(?:возвращ\w*|окончан\w*|завершен\w*|прибыт\w*|' + _EVENT + r')'
                        r'|\bпо\s+(?:возвращ\w*|окончан\w*|завершен\w*|прибыт\w*)'
                        r'|\bсо\s+дня\s+(?:возвращ|прибыт|окончан|завершен)\w*', re.I)
# Section headings may name a stage without a preposition.
BEFORE_HEADING = re.compile(r'\bподготовк\w*|\bнаправлени\w*\s+(?:в|на)\s+\w+|\bпланировани\w*\s+(?:поездк|командир|выезд)\w*', re.I)
AFTER_HEADING = re.compile(r'\bвозвращ\w*|\bзакрыти\w*|\bотч[её]тност\w*|\bзавершени\w*|\bокончани\w*|\bпо\s+итогам\b', re.I)

FORM = re.compile(
    r'\bформ\w*\s+(?:№\s*)?[a-zа-яё]{0,5}[-‑–]?\d'
    r'|\bф\.\s*(?:№\s*)?[a-zа-яё]{0,5}[-‑–]?\d'
    r'|\bбланк\w*\s+(?:№\s*)?[a-zа-яё]{0,5}[-‑–]?\d'
    r'|\bформ\w*[^.;]{0,40}?приложени\w*\s*(?:№\s*)?\d'
    r'|\b(?:отч[её]т|заявлени|записк|решени|приказ)\w*\s+(?:по\s+)?(?:№\s*)?\d{5,}', re.I)
_NUMBER = (r'(?:\d+|одн\w*|дв[аеухум]*|тр[иеёх]\w*|четыр\w*|пят\w*|шест\w*|сем[иь]\w*|'
           r'восьм\w*|восем\w*|девят\w*|десят\w*|пятнадцат\w*|двадцат\w*|тридцат\w*)')
TIME = re.compile(
    _NUMBER + r'\s+(?:рабоч\w*\s+|календарн\w*\s+)?(?:дн\w*|ден\w*|час\w*|сут\w*|недел\w*|месяц\w*)'
    r'|\b(?:\d+[-‑–]?\s*|одно|двух|трех|трёх|пяти|десяти)(?:дневн|суточн|часов)\w*\s+срок'
    r'|\bв\s+день\s+(?:выезд|отъезд|поездк|возвращ|прибыт)\w*'
    r'|\bне\s+позднее\s+(?:дня|даты)\s+\w+', re.I)
ACTION = re.compile(r'пода\w*|направ\w*|представ\w*|переда\w*|отправ\w*|заполн\w*|оформ\w*|'
                    r'согласу\w*|сда\w*|составля\w*|получа\w*|утвержда\w*', re.I)
CONDITION = re.compile(r'^(?:[\d.]+\s*)?(?:если|при\s|в\s+случае|за\s+исключением|кроме|исключение|'
                       r'однако|допускается)|\bесли\b|\bв\s+случае\b|\bза\s+исключением\b', re.I)
FACETS = (('действие', ACTION), ('номер формы', FORM), ('срок', TIME))

_NUMBERED = re.compile(r'^(?:\d{1,2}(?:\.\d{1,3})*\.?|[а-яa-z]\)|[-–•])\s+', re.I)
_APPENDIX_LIST = re.compile(r'^(?:перечень\s+|список\s+)?приложени[йяе]\b', re.I)
_HEADING = re.compile(r'^(?:\d{1,2}\.?|раздел\s+\S+|глава\s+\S+)\s+[А-ЯЁA-Z][^.;:]{2,90}$')
_BOUNDARY = re.compile(r'(?<=[.!?])\s+(?=[А-ЯЁA-Z«])')
_ABBREVIATION = re.compile(r'(?:^|[\s(])(?:[а-яёa-z]|ст|пп|гг|см|им|руб|тыс|млн|стр|прил|напр|т\.\s?[дпе])\.$', re.I)


def _sentences(text):
    out, start = [], 0
    for m in _BOUNDARY.finditer(text):
        if _ABBREVIATION.search(text[max(0, m.start() - 8):m.start()]):
            continue
        out.append(text[start:m.start()]); start = m.end()
    out.append(text[start:])
    return out


QUESTION_BEFORE = re.compile(r'\b(?:до|перед)\s+|\bзаранее\b|\bзаблаговременно\b|\bпредварительно\b|'
                             r'\bкогда\s+(?:пода\w*|оформ\w*|переда\w*)', re.I)
QUESTION_AFTER = re.compile(r'\bпосле\s+|\bпо\s+(?:е[её]\s+|его\s+|их\s+)?(?:возвращ|окончан|завершен|прибыт|итогам|результатам)\w*', re.I)


def staged(question):
    return bool(QUESTION_BEFORE.search(question) and QUESTION_AFTER.search(question))


# The event a staged question is about: "перед аттестацией … после неё",
# "до начала инвентаризации и по её итогам". Pronouns refer to it.
_EVENT_NOUN = r'(?:(?:проведени|начал|окончани|завершени|прохождени|оформлени)\w*\s+)?'
_QUESTION_EVENT = re.compile(r'\b(?:до|перед|после|по\s+(?:итогам|результатам|окончании|завершении))\s+'
                             + _EVENT_NOUN + r'(?:е[её]\s+|его\s+)?([а-яё]{4,})', re.I)
_NOT_EVENT = re.compile(r'^(?:него|нее|неё|этого|этой|того|той|них|них|итогам|результатам|начала|окончания|'
                        r'завершения|проведения|даты|дня|срока|момента)$')


def question_events(question):
    """Stems of the events named around "до/перед/после" in a question."""
    from .sufficiency import _stem
    out = []
    for m in _QUESTION_EVENT.finditer(question.lower().replace('ё', 'е')):
        word = m.group(1)
        if _NOT_EVENT.match(word):
            continue
        stem = _stem(word)
        if len(stem) >= 4 and stem not in out:
            out.append(stem)
    return tuple(out)


def _event_patterns(events):
    if not events:
        return None, None
    event = '(?:' + '|'.join(re.escape(e) for e in events) + r')\w*'
    before = re.compile(r'\b(?:до|перед)\s+' + _EVENT_NOUN + event, re.I)
    after = re.compile(r'\bпосле\s+' + _EVENT_NOUN + event
                       + r'|\bпо\s+(?:итогам|результатам|окончании|завершении)\s+' + _EVENT_NOUN + event, re.I)
    return before, after


def marker(text, events=()):
    """Stage named by an explicit event marker in one unit, or None.

    Besides trip events, markers of the events named in the question count."""
    a, b = BEFORE_TEXT.search(text), AFTER_TEXT.search(text)
    before, after = _event_patterns(events)
    if before is not None:
        a = a or before.search(text)
        b = b or after.search(text)
    if a and not b: return 'до'
    if b and not a: return 'после'
    return None


def stage(text, events=()):
    """Stage of a standalone text without document context."""
    return marker(text, events)


def _heading_stage(text, events=()):
    explicit = marker(text, events)
    if explicit: return explicit
    a, b = BEFORE_HEADING.search(text), AFTER_HEADING.search(text)
    if a and not b: return 'до'
    if b and not a: return 'после'
    return None


def _split(text, metadata):
    """Cut a fragment into numbered items or sentences with offsets."""
    if metadata.get('type') not in ('pdf_text', 'ocr'):
        if not text.strip():
            return []
        # A paragraph that names two stages is cut into sentences.
        if BEFORE_TEXT.search(text) and AFTER_TEXT.search(text):
            return [locate(text, piece.strip()) for piece in _sentences(text.strip()) if piece.strip()]
        return [(0, len(text), text.strip())]
    lines, pos = [], 0
    for line in text.split('\n'):
        lines.append((pos, line)); pos += len(line) + 1
    blocks = []  # (start, end, joined text)
    for start, line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        prev = blocks[-1] if blocks else None
        # A wrapped line continues the previous block when it starts in lower
        # case or with a non-letter (an address, a number); a heading-like
        # previous line is closed only by a capitalised or numbered line.
        upper = stripped[:1].isupper()
        starts_new = (prev is None or _NUMBERED.match(stripped) or prev[2].endswith(':')
                      or (upper and (_HEADING.match(prev[2]) or _HEADING.match(stripped)
                                     or prev[2].endswith(('.', ';')) or prev[2].isupper())))
        if starts_new:
            blocks.append([start, start + len(line), stripped])
        else:
            prev[1] = start + len(line); prev[2] = prev[2] + ' ' + stripped
    out = []
    for start, end, joined in blocks:
        pieces = _sentences(joined) if not _NUMBERED.match(joined) or len(joined) > 400 else [joined]
        for piece in pieces:
            if piece.strip():
                out.append(locate(text, piece.strip(), start, end))
    return out


def locate(text, piece, start=0, end=None):
    """Exact character range of a unit inside its fragment (line breaks may differ)."""
    end = len(text) if end is None else end
    pattern = r'\s+'.join(re.escape(word) for word in piece.split())
    match = re.search(pattern, text[start:end]) if pattern else None
    if match:
        return (start + match.start(), start + match.end(), piece)
    return (start, end, piece)


def version_units(db, source_id, version, events=()):
    """Units of one imported version, in document order, with stage context.

    ``events``: stems of the events named in the question (question_events)."""
    rows = db.execute('SELECT rowid AS rowid, * FROM import_fragments WHERE source_id=? AND version=? ORDER BY rowid',
                      (source_id, version)).fetchall()
    units, section, seen = [], None, set()
    tables = {}  # table locator prefix -> header cells
    for row in rows:
        metadata = json.loads(row['metadata'] or '{}')
        kind = metadata.get('type')
        if kind in ('footnote', 'cell'):
            continue
        header = None
        if kind == 'table_row':
            prefix = row['locator'].rsplit('/row/', 1)[0]
            if prefix not in tables:
                # The first row of a table names its columns.
                tables[prefix] = [str(c) for c in metadata.get('cells', [])]
                continue
            header = tables[prefix]
        for start, end, text in _split(row['text'] or '', metadata):
            heading = bool(_HEADING.match(text) or (kind == 'paragraph' and str(metadata.get('style') or '').lower().startswith(('heading', 'title', 'заголов'))))
            if heading:
                section = _heading_stage(text, events)
                continue
            if _APPENDIX_LIST.match(text):
                section = None  # a list of appendices closes the procedure sections
            key = re.sub(r'\s+', ' ', text.lower())
            if key in seen:
                continue  # the OCR layer may repeat the text layer of a page
            seen.add(key)
            own = marker(text, events)
            unit_stage = own or section
            if own and kind != 'table_row':
                # Following items of the same section continue the named stage
                # until a heading or an item naming another stage.
                section = own
            facets = [name for name, pattern in FACETS if pattern.search(text)]
            if header:
                for column, value in zip(header, metadata.get('cells', [])):
                    if not str(value).strip(): continue
                    if re.search(r'форм|бланк', column, re.I) and re.search(r'\d', str(value)) and 'номер формы' not in facets:
                        facets.append('номер формы')
                    if re.search(r'срок', column, re.I) and TIME.search(str(value)) and 'срок' not in facets:
                        facets.append('срок')
                    if re.search(r'действ|что\s+сделать|мероприят', column, re.I) and 'действие' not in facets:
                        facets.append('действие')
            units.append({'id': row['id'], 'source_id': source_id, 'version': version, 'locator': row['locator'], 'text': text, 'start': start, 'end': end,
                          'header': header,
                          'stage': unit_stage, 'facets': facets, 'condition': bool(CONDITION.search(text)),
                          # A unit that itself names the stage ("После аттестации
                          # работник …") is a step even without a listed verb.
                          'marked': bool(own),
                          'metadata': metadata, 'rowid': row['rowid']})
    return units


def units(db, fragment):
    """Units cut from one fragment, staged with the context of its version."""
    return [u for u in version_units(db, fragment['source_id'], fragment['version']) if u['id'] == fragment['id']]


def select_units(all_units, per_stage=10):
    """Keep units that state an action, form, deadline or condition of a stage."""
    selected = []
    for name in ('до', 'после'):
        stage_units = [u for u in all_units if u['stage'] == name and (u['facets'] or u['condition'] or u.get('marked'))]
        chosen = []
        # Make sure each facet present in the stage is represented, then fill in order.
        for facet, _ in FACETS:
            first = next((u for u in stage_units if facet in u['facets'] and u not in chosen), None)
            if first is not None: chosen.append(first)
        for u in stage_units:
            if len(chosen) >= per_stage: break
            if u not in chosen: chosen.append(u)
        selected.extend(sorted(chosen, key=lambda u: (u['rowid'], u['start'])))
    return selected


ASK_TIME = re.compile(r'\bкогда\b|\bсрок\w*|сколько|за\s+какое|в\s+течение', re.I)
ASK_FORM = re.compile(r'\bформ\w*|\bбланк\w*|\bдокумент\w*|\bчто\s+(?:заполн|подать|сдать|представить)\w*', re.I)


def requested_facets(question):
    """Facets a staged question asks about: an action always; a deadline or a
    form only when the question asks for them (ANS-07: name the gaps of what
    was asked, not of everything a procedure might contain)."""
    if question is None:
        return {label for label, _ in FACETS}
    out = {'действие'}
    if ASK_TIME.search(question): out.add('срок')
    if ASK_FORM.search(question): out.add('номер формы')
    return out


def missing(items, question=None):
    """Flag unsupported facets, without inventing form IDs, steps or deadlines.

    Items are units with a 'stage' key, or plain texts staged by explicit markers.
    A unit that names its stage itself counts as the action of that stage.
    """
    wanted = requested_facets(question)
    out = []
    for name in ('до', 'после'):
        group = [item for item in items if (item.get('stage') if 'stage' in item else stage(item['text'])) == name]
        for label, pattern in FACETS:
            if label not in wanted:
                continue
            if not any(pattern.search(item['text']) or label in (item.get('facets') or [])
                       or (label == 'действие' and item.get('marked')) for item in group):
                out.append(f'{name}: {label}')
    return out


_BLANK = re.compile(r'«\s*_{1,}\s*»|№\s*_{2,}|_{3,}\s*20_*\s*г')
_APPENDIX = re.compile(r'приложени\w*\s*(?:№\s*)?(\d{1,2})\b', re.I)


def source_limits(db, items):
    """Limitations confirmed by the text of the cited versions.

    Returns (text, fragment_id) pairs: blank approval requisites and referenced
    appendices whose body is absent from the imported version.
    """
    out, done = [], set()
    for source_id, version in dict.fromkeys((i['source_id'], i['version']) for i in items):
        rows = db.execute('SELECT id, text FROM import_fragments WHERE source_id=? AND version=? ORDER BY rowid',
                          (source_id, version)).fetchall()
        blank = next((r for r in rows if _BLANK.search(r['text'] or '')), None)
        if blank is not None:
            out.append(('В тексте документа не заполнены реквизиты утверждения (дата или номер); '
                        'статус утверждения по импортированному файлу не подтверждён.', blank['id']))
        # An appendix body normally starts with its own heading line.
        bodies = set()
        for r in rows:
            for line in (r['text'] or '').split('\n'):
                m = re.match(r'\s*приложени\w*\s*(?:№\s*)?(\d{1,2})\s*(?:$|к\s|\()', line, re.I)
                if m: bodies.add(m.group(1))
        for item in items:
            if (item['source_id'], item['version']) != (source_id, version): continue
            for number in _APPENDIX.findall(item['text']):
                key = (source_id, version, number)
                if number in bodies or key in done: continue
                done.add(key)
                out.append(('Приложение ' + number + ' упомянуто в пункте, но его текст в импортированной версии '
                            'не найден; бланк или форму сверьте по оригиналу.', item['id']))
    return out


def expand_items(db, items, key='basis'):
    """Stage curated or selected items through the units of their fragments."""
    out = []
    for item in items:
        row = db.execute('SELECT * FROM import_fragments WHERE id=?', (item[key],)).fetchone() if item.get(key) else None
        cut = [u for u in units(db, row) if u['stage']] if row is not None else []
        if cut:
            out.extend(dict(item, text=u['text'], stage=u['stage'], condition=u['condition'],
                            start=u['start'], end=u['end']) for u in cut)
        else:
            out.append(dict(item, stage=stage(item['text']), condition=bool(CONDITION.search(item['text']))))
    return out
