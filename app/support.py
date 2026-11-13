"""Extractive answers to "a device or IT service is faulty: where and how to report".

Evidence is chosen at the level of numbered items or sentences of imported
documents (see procedures.version_units). A document qualifies only if its own
text states a reporting channel together with the request content or the
processing time, and it speaks about faults. Budget tables, purchase rules and
other documents that merely share vocabulary do not qualify. Several qualifying
documents are shown separately; the applicable one is not chosen silently.
"""
import re

from .procedures import version_units

DEVICE = (r'принтер\w*|мфу|сканер\w*|ксерокс\w*|копир\w*|компьютер\w*|ноутбук\w*|монитор\w*|'
          r'оргтехник\w*|печат\w*\s+(?:устройств|техник)\w*|клавиатур\w*|мыш[ьи]\w*|проектор\w*|'
          r'телефон\w*|интернет\w*|сет[ьи]\b|wi-?fi|вай-?фай|почт\w*|учётн\w*\s+запис\w*|учетн\w*\s+запис\w*|'
          r'програм\w*|устройств\w*|оборудован\w*')
FAULT = (r'не\s+работа\w*|не\s+включа\w*|не\s+печата\w*|не\s+сканир\w*|не\s+открыва\w*|не\s+загружа\w*|'
         r'неисправ\w*|сломал\w*|слома\w*|поломк\w*|сбо[йяеи]\w*|завис\w*|ошибк\w*|почин\w*|отремонт\w*|'
         r'ремонт\w*|глюч\w*|отказ\w*\s+работ')
REPORT = r'куда\s+(?:обрат|сообщ|пис|подат|направ|звон)\w*|как\s+(?:сообщ|обрат|подат|оформ)\w*'

# Facets of the evidence.
CHANNEL = re.compile(r'https?://|[\w.-]+@[\w-]+\.[\w.-]+|\b[\w-]+\.(?:invalid|ru|com|org|local)\b|портал\w*|'
                     r'helpdesk|сервис\s*деск|систем\w*\s+(?:обращени|заявок)\w*|электронн\w*\s+почт\w*', re.I)
REQUEST = re.compile(r'канал\w*|обращени\w*|заявк\w*|сообща\w*|сообщени\w*|регистрир\w*|пода\w*|направля\w*|обраща\w*', re.I)
DETAIL_NOUNS = re.compile(r'фио|фамили\w*|телефон\w*|кабинет\w*|корпус\w*|местоположен\w*|подразделен\w*|'
                          r'описани\w*|инвентарн\w*|модел\w*|скриншот\w*|снимок\s+экрана|почт\w*|номер\w*\s+устройств', re.I)
DETAIL_VERB = re.compile(r'указыва\w*|указать|содерж\w*|приклад\w*|приложи\w*|сообща\w*|включа\w*', re.I)
DURATION = re.compile(r'\d+\s+(?:рабоч\w*\s+|календарн\w*\s+)?(?:час\w*|дн\w*|ден\w*|сут\w*|недел\w*)', re.I)
PROCESSING = re.compile(r'первичн\w*\s+(?:\w+\s+){0,2}обработ\w*|обрабатыва\w*|рассматрива\w*|рассмотр\w*|'
                        r'регистрир\w*|реагир\w*', re.I)
NOT_REPAIR = re.compile(r'не\s+(?:является|означает|считается)\s+срок\w*\s+(?:устранени|ремонт|восстановлени)', re.I)
REPAIR_TIME = re.compile(r'устранени\w*|устраня\w*|ремонт\w*|восстановлени\w*|почин\w*', re.I)
SCOPE = re.compile(r'не\s+(?:обслужива|принима|рассматрива|считаю|являю|выполня)\w*|не\s+входит', re.I)
OTHER_REQUEST = re.compile(r'(?:заявк|обращени)\w*\s+на\s+(?!ремонт|устранени)\w+|закупк\w*|замен\w*\s+\w+|заправк\w*', re.I)
FAULT_TEXT = re.compile(r'неисправ\w*|сбо[йяеи]\w*|поломк\w*|отказ\w*|ремонт\w*|устранени\w*|не\s+работа\w*', re.I)

ASK_CHANNEL = re.compile(r'куда|кому|где\s+(?:подать|оставить|оформить)|как\s+(?:сообщ|обрат|подат|оформ|написа)\w*|'
                         r'через\s+что|какой\s+канал', re.I)
ASK_DETAILS = re.compile(r'что\s+(?:написать|указать|сообщить|приложить|писать)|как\w*\s+(?:данн|сведени|информаци)\w*|'
                         r'что\s+нужно\s+(?:написать|указать)|состав\w*\s+(?:заявк|обращени)', re.I)
ASK_TIME = re.compile(r'срок\w*|сколько|когда|как\s+долго|как\s+быстро|через\s+какое', re.I)
ASK_REPAIR = re.compile(r'почин\w*|отремонт\w*|ремонт\w*|устран\w*|исправ\w*|восстанов\w*', re.I)


def support_request(question):
    q = question.lower().replace('ё', 'е')
    device = re.search(DEVICE, q)
    return bool(device and (re.search(FAULT, q) or re.search(REPORT, q) and re.search(r'неисправ|слома|не\s+работ', q)))


def _facets(unit):
    text = unit['text']
    out = set()
    if CHANNEL.search(text) and REQUEST.search(text):
        out.add('channel')
    if DETAIL_NOUNS.search(text) and DETAIL_VERB.search(text) and not CHANNEL.search(text):
        out.add('details')
    if PROCESSING.search(text) and DURATION.search(text):
        out.add('processing')
    if NOT_REPAIR.search(text):
        out.add('not_repair')
    elif REPAIR_TIME.search(text) and DURATION.search(text) and not SCOPE.search(text):
        out.add('repair_time')
    if SCOPE.search(text) and (REQUEST.search(text) or re.search(r'устройств|оборудован|звонк|мессенджер', text, re.I)):
        out.add('scope')
    return out


def _table_channel(unit):
    header = ' '.join(unit.get('header') or [])
    return bool(header and re.search(r'канал|адрес|контакт|способ\s+обращени', header, re.I) and CHANNEL.search(unit['text']))


def candidates(db):
    """Qualifying support documents with their faceted units."""
    versions = db.execute('''SELECT source_id, version, filename FROM imports i
        WHERE (SELECT count(*) FROM imports v WHERE v.source_id=i.source_id)=1 ORDER BY rowid''').fetchall()
    result = []
    for v in versions:
        units = version_units(db, v['source_id'], v['version'])
        if not units:
            continue
        faceted = [(u, _facets(u) | ({'channel'} if _table_channel(u) else set())) for u in units]
        kinds = set().union(*(f for _, f in faceted))
        fault_doc = any(FAULT_TEXT.search(u['text']) for u in units)
        if 'channel' not in kinds or not ({'details', 'processing'} & kinds) or not fault_doc:
            continue
        # The channel must be stated for reporting faults or requests in
        # general, not for an unrelated operation such as a purchase.
        channels = [u for u, f in faceted if 'channel' in f]
        if all(re.search(r'закупк\w*|приобрет\w*|покупк\w*', u['text'], re.I) for u in channels):
            continue
        result.append({'source_id': v['source_id'], 'version': v['version'], 'title': v['filename'],
                       'units': faceted, 'text': '\n'.join(u['text'] for u in units)})
    return result


def _words(text):
    return re.findall(r'[а-яёa-z0-9]+', text.lower().replace('ё', 'е'))


def _part_kind(part):
    """Which support facets a question part asks for, or None for another action."""
    kinds = set()
    if ASK_CHANNEL.search(part): kinds.add('channel')
    if ASK_DETAILS.search(part): kinds.add('details')
    if ASK_TIME.search(part): kinds.add('time')
    if not kinds and re.search(FAULT, part.lower()):
        kinds.add('channel')
    return kinds


_OWN = re.compile(r'^(?:' + '|'.join(['куда', 'кому', 'как', 'что', 'где', 'какие', 'какой', 'какую', 'когда', 'сколько',
                                       'нужно', 'надо', 'можно', 'обратиться', 'сообщить', 'написать', 'указать',
                                       'подать', 'заявку', 'заявка', 'данные', 'сведения', 'обращение', 'обратится',
                                       'через', 'срок', 'сроки', 'писать', 'это', 'мне', 'если', 'тогда', 'нам']) + r')$')


REPORTING = {'обратиться', 'обращаться', 'сообщить', 'сообщать', 'написать', 'писать', 'указать', 'указывать',
             'подать', 'подавать', 'оформить', 'направить', 'отправить', 'оставить', 'ждать', 'приложить',
             'звонить', 'позвонить', 'обратится', 'делать', 'быть'}


def _independent(part):
    """A part that asks for another action than reporting the fault."""
    q = part.lower().replace('ё', 'е')
    if re.search(FAULT, q):
        return False
    verbs = [w for w in _words(q) if re.fullmatch(r'[а-я]{3,}(?:ть|ти|ться|тись)', w)]
    return any(v not in REPORTING for v in verbs)


def _relevant_units(source, part):
    """Units of a qualifying document that mention the object of an extra part."""
    from .sufficiency import _stem
    terms = [_stem(w) for w in _words(part) if len(w) >= 5 and not _OWN.match(w)]
    terms = [t for t in terms if not re.match(r'(?:заказ|получ|замен|оформ|сделат|подат|узнат)', t)
             and not re.search(r'(?:ый|ий|ой|ая|яя|ое|ее|ые|ие)$', t)]
    if not terms:
        return []
    out = []
    for unit, _ in source['units']:
        words = [_stem(w) for w in _words(unit['text'])]
        if all(any(w.startswith(t[:6]) for w in words) for t in terms):
            out.append(unit)
    return out[:3]


def answer(db, question):
    """Return a structured extractive answer or None when not a support request."""
    if not support_request(question):
        return None
    from .questions import split_question
    from .sufficiency import supports
    from .retrieval import imported, filter_sibling_cells
    parts = split_question(question)
    support_parts = [p for p in parts if not _independent(p)] or parts[:1]
    extra_parts = [p for p in parts if p not in support_parts]
    asked = set().union(*(_part_kind(p) for p in support_parts)) or {'channel', 'details'}
    asks_repair = bool(ASK_REPAIR.search(question) and ASK_TIME.search(question))
    if asks_repair:
        asked.add('time')
    sources = candidates(db)
    device = re.search(DEVICE, question.lower())
    device_stem = device.group(0)[:5] if device else None
    if len(sources) > 1 and device_stem:
        # A document naming the device stays first; the others are still shown.
        sources.sort(key=lambda s: device_stem not in s['text'].lower(), reverse=False)
    result = {'sources': [], 'extra': [], 'gaps': [], 'notes': []}
    for source in sources:
        facet_units = {}
        for unit, facets in source['units']:
            for facet in facets:
                facet_units.setdefault(facet, []).append(unit)
        # A channel stated for faults or for requests in general comes first;
        # a channel for another kind of request (a replacement, a purchase) is
        # shown only when nothing more specific exists.
        channels = facet_units.get('channel', [])
        specific = [u for u in channels if FAULT_TEXT.search(u['text'])]
        general = [u for u in channels if u not in specific and not OTHER_REQUEST.search(u['text'])]
        channels = (specific + general) or channels
        shown = {
            'channel': channels[:3],
            'details': facet_units.get('details', [])[:3],
            'time': (facet_units.get('processing', []) + facet_units.get('not_repair', []) + facet_units.get('repair_time', []))[:4],
            'scope': facet_units.get('scope', [])[:3],
        }
        # De-duplicate units that carry several facets, keep the first facet.
        seen = set()
        for key in ('channel', 'details', 'time', 'scope'):
            kept = []
            for unit in shown[key]:
                ident = (unit['id'], unit['text'])
                if ident in seen: continue
                seen.add(ident); kept.append(unit)
            shown[key] = kept
        missing = []
        if 'channel' in asked and not shown['channel']: missing.append('канал обращения')
        if 'details' in asked and not shown['details']: missing.append('состав обращения')
        if 'time' in asked and not facet_units.get('processing') and not facet_units.get('repair_time'):
            missing.append('срок рассмотрения обращения')
        limits = []
        if not facet_units.get('repair_time'):
            if asks_repair:
                missing.append('срок устранения неисправности — в тексте документа не установлен')
            elif shown['time'] and not facet_units.get('not_repair'):
                limits.append('указан срок рассмотрения обращения; срок устранения неисправности в тексте документа не установлен.')
        result['sources'].append({'title': source['title'], 'source_id': source['source_id'],
                                  'version': source['version'], 'facets': shown, 'missing': missing, 'limits': limits})
    if len(result['sources']) > 1:
        channels = [tuple(sorted(set(m.group(0).lower() for u in s['facets']['channel'] for m in CHANNEL.finditer(u['text']))))
                    for s in result['sources']]
        note = 'Найдено несколько документов о подаче обращений; применимый документ по тексту не установлен.'
        if len(set(channels)) > 1:
            note += ' Каналы в них различаются — уточните, к какому оборудованию или подразделению относится обращение.'
        result['notes'].append(note)
    for part in extra_parts:
        rows = []
        for source in sources:
            rows.extend({'text': u['text'], 'id': u['id'], 'start': u['start'], 'end': u['end']} for u in _relevant_units(source, part))
        if not rows:
            found = filter_sibling_cells(imported(db, part, k=3), part)
            rows = [{'text': r['text'], 'id': r['id']} for r in found
                    if r['text'].strip() and len(r['text']) <= 2000 and supports(part, r['text'])
                    and FAULT_TEXT.search(r['text']) is None and _meta_type(db, r['id']) != 'cell']
        result['extra'].append({'part': part, 'items': rows[:3]})
        if not rows:
            result['gaps'].append(part)
    if not result['sources']:
        result['gaps'] = support_parts + result['gaps']
    return result


def _meta_type(db, fragment_id):
    import json
    row = db.execute('SELECT metadata FROM import_fragments WHERE id=?', (fragment_id,)).fetchone()
    return json.loads(row['metadata']).get('type') if row else None
