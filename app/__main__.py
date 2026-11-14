"""Local-only HTTP interface and fixture import."""
from contextlib import closing
import argparse
import html
import os
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, quote, urlparse
from .core import DEFAULT_DB, connect, connect_existing, form_answer, import_fixture, imported_parts

DB = os.environ.get('NAVIGATOR_DB', DEFAULT_DB)

def esc(value):
    return html.escape(str(value), quote=True)

def import_notes(metadata):
    notes = []
    if metadata.get('unreadable'): notes.append('OCR не распознал текст; требуется визуальная проверка оригинала.')
    if metadata.get('formula_error'): notes.append('Формула содержит повреждённую ссылку; результат не подтверждён.')
    if metadata.get('requires_visual_review') and not metadata.get('unreadable'): notes.append('OCR требует визуальной сверки оригинала.')
    if metadata.get('type') == 'pdf_text' and metadata.get('ocr_needed'): notes.append('Текстовый слой может быть неполным; требуется сверка оригинала.')
    return notes

def fragment_href(fragment_id, item=None):
    """Link to an imported fragment; a unit inside it is passed as a character range."""
    href = '/fragment?id=' + quote(fragment_id, safe='')
    if item is not None and isinstance(item.get('start'), int) and isinstance(item.get('end'), int) and item['end'] > item['start']:
        href += '&amp;at=' + str(item['start']) + '-' + str(item['end'])
    return href

def cite(fragment_id, item=None):
    return ' <a href="' + fragment_href(fragment_id, item) + '">' + esc(fragment_id) + '</a>'

def highlighted(text, at):
    if at:
        start, end = at
        if 0 <= start < end <= len(text):
            return esc(text[:start]) + '<mark id="cited">' + esc(text[start:end]) + '</mark>' + esc(text[end:])
    return esc(text)

def render_import_fragment(row, selected, at=None):
    import json
    metadata = json.loads(row['metadata'])
    headers = ''
    for key, label in (('column_headers','Заголовки столбца'), ('row_headers','Заголовки строки')):
        if metadata.get(key):
            headers += '<p>' + label + ': ' + esc('; '.join(x['coordinate'] + ': ' + x['text'] for x in metadata[key])) + '</p>'
    if metadata.get('header_inferred') and headers:
        headers += '<p>Заголовки определены предположительно; требуется сверка.</p>'
    if metadata.get('formula') is not None:
        headers += '<p>Формула: ' + esc(metadata['formula']) + '; сохранённое значение: ' + esc(metadata.get('cached_value') if metadata.get('cached_value') is not None else 'отсутствует') + '</p>'
    return '<div' + (' id="selected"' if selected else '') + '><p><strong>' + esc(row['locator']) + '</strong> ' + highlighted(row['text'], at if selected else None) + ''.join(' <em>' + esc(note) + '</em>' for note in import_notes(metadata)) + '</p>' + headers + '</div>'

STAGE_HEADINGS = (('До поездки или события', 'до'), ('После возвращения или события', 'после'))

def render_stages(items, other_heading, link):
    """Group staged units; conditions and exceptions are shown inside their stage."""
    content = ''
    for heading, key in STAGE_HEADINGS + ((other_heading, None),):
        group = [item for item in items if item.get('stage') == key]
        if not group: continue
        content += '<h4>' + heading + '</h4>'
        steps = [item for item in group if not item.get('condition')]
        conditions = [item for item in group if item.get('condition')]
        if steps:
            content += '<ol>' + ''.join('<li>' + esc(item['text']) + link(item['basis'], item) + '</li>' for item in steps) + '</ol>'
        if conditions:
            content += '<h5>Условия и исключения этого этапа</h5><ul>' + ''.join('<li>' + esc(item['text']) + link(item['basis'], item) + '</li>' for item in conditions) + '</ul>'
    return content

SUPPORT_HEADINGS = (('channel', 'Куда обращаться'), ('details', 'Что указать в обращении'),
                    ('time', 'Сроки рассмотрения'), ('scope', 'Ограничения из текста документа'))

def render_support(support, import_meta, source_notes):
    def link(fid, item=None):
        return ''.join(' <em>' + esc(note) + '</em>' for note in import_notes(import_meta.get(fid, {}))) + cite(fid, item)
    content = '<h2>Найденные сведения об обращении</h2><p>Применимость и полнота не проверены; дата вступления и связь редакций не подтверждены. Сверьте источник.</p>'
    for note in support['notes']:
        content += '<p>Ограничение: ' + esc(note) + '</p>'
    many = len(support['sources']) > 1
    for number, source in enumerate(support['sources'], 1):
        if many: content += '<h3>Документ ' + str(number) + ': ' + esc(source['title']) + '</h3>'
        for key, heading in SUPPORT_HEADINGS:
            units = source['facets'][key]
            if units:
                content += '<h4>' + heading + '</h4><ol>' + ''.join('<li>' + esc(u['text']) + link(u['id'], u) + '</li>' for u in units) + '</ol>'
        for note in source.get('limits', []):
            content += '<p>Ограничение: ' + esc(note) + '</p>'
        for gap in source['missing']:
            content += '<p>Недостаточно данных для части' + (' (' + esc(source['title']) + ')' if many else '') + ': ' + esc(gap) + '.</p>'
    for extra in support['extra']:
        content += '<h3>' + esc(extra['part']) + '</h3>'
        if extra['items']:
            content += '<ol>' + ''.join('<li>' + esc(i['text']) + link(i['id'], i) + '</li>' for i in extra['items']) + '</ol>'
    for gap in support['gaps']:
        content += '<p>Недостаточно данных для части вопроса: ' + esc(gap) + '.</p>'
    for note, fid in source_notes:
        content += '<p>Ограничение: ' + esc(note) + ' <a href="/fragment?id=' + quote(fid, safe='') + '">' + esc(fid) + '</a></p>'
    return content

def render_rag(db, result, when):
    """HTML of a verified RAG answer; every statement links to its chunk address."""
    status = result['status']
    if status == 'off_topic':
        return ('<p>Вопрос не относится к внутренним документам и регламентам организации; '
                'навигатор отвечает только по ним.</p>')
    if status == 'insufficient' or not result['items']:
        return '<p>Недостаточно данных для ответа в доступных материалах.</p>'
    content = '<h2>Ответ</h2>'
    if result['short_answer'] and not (len(result['items']) == 1 and result['short_answer'].strip(' .') == result['items'][0]['text'].strip(' .')):
        content += '<h3>' + esc(result['short_answer']) + '</h3>'
    content += '<ol>'
    for item in result['items']:
        content += '<li>' + esc(item['text']) + ''.join(cite(c['fragment_id'], {'start': c['start'], 'end': c['end']})
                                                       for c in item['chunks']) + '</li>'
    content += '</ol>'
    # The model fills "missing" with noise on full answers: a gap is shown only
    # when it declared the answer partial.
    if status == 'partial':
        content += '<p>Недостаточно данных для части вопроса: ' + esc('; '.join(result['missing']) or 'см. вопрос') + '.</p>'
    if result['dropped']:
        content += ('<p>Самопроверка: ' + str(len(result['dropped'])) + ' утвержд. модели не подтверждены текстом '
                    'источников и не показаны.</p>')
    keys = list(dict.fromkeys((c['source_id'], c['version']) for item in result['items'] for c in item['chunks']))
    for key in keys:
        if result['headers'].get(key):
            content += '<p>Источник: ' + esc(result['headers'][key]) + '.</p>'
    from .requisites import status as version_status
    for _, (state, notes) in version_status(db, keys, when).items():
        for note, fid in notes:
            content += '<p>Ограничение: ' + esc(note) + (cite(fid) if fid else '') + '</p>'
    content += ('<p>Применимость и полнота проверены по тексту найденных документов; ответ сформирован локальной '
                'моделью и проверен по цитатам; сверьте источник.</p>')
    return content


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Questions and document text are not logged.

    def page(self, content, status=200):
        body = ('<!doctype html><html lang="ru"><meta charset="utf-8"><title>Навигатор</title><body><h1>Навигатор сотрудника</h1>' + content + '</body></html>').encode()
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def form(self, question=''):
        return '<form method="post" action="/ask"><label>Вопрос <input name="question" maxlength="2000" value="' + esc(question) + '"></label> <label>Дата <input type="date" name="date"></label> <label>Категория <input name="category"></label><button>Найти</button></form>'

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/':
            return self.page(self.form())
        if parsed.path != '/fragment':
            return self.page('Страница не найдена', 404)
        fragment_id = parse_qs(parsed.query).get('id', [''])[0]
        cited_range = None
        at = re.fullmatch(r'(\d{1,7})-(\d{1,7})', parse_qs(parsed.query).get('at', [''])[0])
        if at:
            cited_range = (int(at.group(1)), int(at.group(2)))
        try:
            with closing(connect_existing(DB)) as db, db:
                tables = {r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'import_fragments' in tables:
                    imported_row = db.execute('SELECT f.*, d.filename AS title FROM import_fragments f JOIN imports d ON d.source_id=f.source_id AND d.version=f.version WHERE f.id=?', (fragment_id,)).fetchone()
                    if imported_row is not None:
                        from .retrieval import context as import_context
                        linked = import_context(db, fragment_id)
                        # Neighbours belong to exactly the same imported version;
                        # linked footnotes remain available after the excerpt.
                        rowid = db.execute('SELECT rowid FROM import_fragments WHERE id=?', (fragment_id,)).fetchone()[0]
                        import json
                        neighbours = [imported_row]
                        if json.loads(imported_row['metadata']).get('type') == 'paragraph':
                            before = db.execute("SELECT * FROM import_fragments WHERE source_id=? AND version=? AND rowid < ? AND locator LIKE 'body/%/paragraph' ORDER BY rowid DESC LIMIT 1", (imported_row['source_id'], imported_row['version'], rowid)).fetchall()
                            after = db.execute("SELECT * FROM import_fragments WHERE source_id=? AND version=? AND rowid > ? AND locator LIKE 'body/%/paragraph' ORDER BY rowid LIMIT 1", (imported_row['source_id'], imported_row['version'], rowid)).fetchall()
                            neighbours = list(reversed(before)) + neighbours + after
                        context = [dict(r) for r in neighbours]
                        seen = {r['id'] for r in context}
                        context.extend(r for r in linked if r['id'] not in seen)
                        content = '<h2>' + esc(imported_row['title']) + '</h2><p>Версия ' + esc(imported_row['version']) + '; выбран ' + esc(imported_row['locator']) + '</p>'
                        content += ''.join(render_import_fragment(r, r['id'] == fragment_id, cited_range) for r in context)
                        return self.page(content + '<a href="/">Новый вопрос</a>')
                try:
                    ev = db.execute('SELECT f.*, d.title FROM evidence_fragments f JOIN evidence_documents d ON d.id=f.document_id WHERE f.id=?', (fragment_id,)).fetchone()
                except sqlite3.OperationalError:
                    ev = None
                if ev:
                    ctx = db.execute('SELECT * FROM evidence_fragments WHERE document_id=? ORDER BY rowid', (ev['document_id'],)).fetchall()
                    return self.page('<h2>' + esc(ev['title']) + '</h2>' + ''.join('<p><strong>' + esc(x['locator']) + '</strong> ' + esc(x['text']) + '</p>' for x in ctx))
                row = db.execute('SELECT f.*, d.title FROM fragments f JOIN documents d ON d.id=f.document_id AND d.version=f.version WHERE f.id=?', (fragment_id,)).fetchone()
                if row is None:
                    try:
                        row = db.execute('SELECT f.*, d.filename AS title FROM import_fragments f JOIN imports d ON d.source_id=f.source_id AND d.version=f.version WHERE f.id=?', (fragment_id,)).fetchone()
                    except sqlite3.OperationalError:
                        row = None
                    if row is None:
                        return self.page('Фрагмент не найден', 404)
                    context = db.execute('SELECT * FROM import_fragments WHERE source_id=? AND version=? ORDER BY rowid', (row['source_id'], row['version'])).fetchall()
                    content = '<h2>' + esc(row['title']) + '</h2><p>Версия ' + esc(row['version']) + '; выбран ' + esc(row['locator']) + '</p>'
                    content += ''.join(render_import_fragment(r, r['id'] == fragment_id, cited_range) for r in context)
                    return self.page(content + '<a href="/">Новый вопрос</a>')
                context = db.execute('SELECT * FROM fragments WHERE document_id=? AND version=? ORDER BY id', (row['document_id'], row['version'])).fetchall()
                content = '<h2>' + esc(row['title']) + '</h2><p>Версия ' + esc(row['version']) + '; выбран ' + esc(row['locator']) + ' (' + esc(row['id']) + ')</p>'
                content += ''.join('<p' + (' id="selected"' if r['id'] == fragment_id else '') + '><strong>' + esc(r['locator']) + '</strong> ' + esc(r['text']) + '</p>' for r in context)
                return self.page(content + '<a href="/">Новый вопрос</a>')
        except (sqlite3.Error, OSError):
            return self.page('Технический сбой при открытии источника. Повторите попытку.', 503)

    def do_POST(self):
        if self.path != '/ask':
            return self.page('Страница не найдена', 404)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or length > 10000:
                return self.page('Слишком длинный запрос.', 400)
            fields = parse_qs(self.rfile.read(length).decode('utf-8'), keep_blank_values=True)
            question = fields.get('question', [''])[0]
            if len(question) > 2000:
                return self.page('Слишком длинный вопрос.', 400)
            if not question.strip():
                return self.page(self.form(question) + '<p>Введите вопрос.</p>', 400)
            with closing(connect_existing(DB)) as db, db:
                tables = {r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                from . import rag
                if rag.enabled() and 'import_fragments' in tables:
                    # RAG pipeline (stage R5); None → the extractive pipeline below.
                    rag_when = fields.get('date', [''])[0] or None
                    if rag_when:
                        from .evidence import iso
                        iso(rag_when)
                    rag_result = rag.answer(db, question, rag_when)
                    if rag_result is not None:
                        return self.page(self.form(question) + render_rag(db, rag_result, rag_when))
                if 'evidence_topics' in tables and 'import_fragments' in tables:
                    from .evidence import query as evidence_query, keyword_matches
                    from .questions import split_question
                    from .procedures import staged
                    parts = [question] if staged(question) else split_question(question)
                    checked = [(p, evidence_query(db, p, fields.get('date',[''])[0] or None, {'категория': fields.get('category',[''])[0]}, imported_only=True)) for p in parts]
                    import json
                    linked_topics = db.execute('''SELECT DISTINCT t.keywords, i.id, i.metadata FROM evidence_topics t
                        JOIN evidence_rules r ON r.topic=t.id
                        JOIN import_fragments i ON i.id=r.fragment_id''').fetchall()
                    matched_link = any(any(keyword_matches(k, part) for k in json.loads(row['keywords']))
                                       for row in linked_topics for part in parts)
                    if matched_link or any(result is not None for _, result in checked):
                        content = self.form(question) + '<h2>Результат проверки</h2>'
                        some_answer = any(result and result['items'] for _, result in checked)
                        incomplete = any(result is None or not result['items'] for _, result in checked)
                        summary = ('Частичный ответ; недостающие части указаны ниже.' if some_answer and incomplete else
                                   'Найдены сведения; условия и ограничения указаны ниже.' if some_answer else
                                   'Подтверждённый ответ не найден; пояснения приведены ниже.')
                        content += '<h3>Краткий результат: ' + esc(summary) + '</h3>'
                        content += '<h4>Условия: дата ' + esc(fields.get('date',[''])[0] or 'не указана') + '; категория ' + esc(fields.get('category',[''])[0] or 'не указана') + '</h4>'
                        def source_link(fid, item=None):
                            return cite(fid, item) if fid else ''
                        for part, checked_result in checked:
                            content += '<h3>' + esc(part) + '</h3>'
                            for linked in linked_topics:
                                if any(keyword_matches(k, part) for k in json.loads(linked['keywords'])):
                                    for note in import_notes(json.loads(linked['metadata'])):
                                        content += '<p>Ограничение: ' + esc(note) + source_link(linked['id']) + '</p>'
                            if checked_result is None:
                                content += '<p>Недостаточно подтверждённых сведений для этой части вопроса: ' + esc(part) + '.</p>'
                                continue
                            if checked_result['items']:
                                if staged(part):
                                    from .procedures import missing as missing_facets, expand_items
                                    expanded = expand_items(db, checked_result['items'])
                                    content += render_stages(expanded, 'Другие подтверждённые сведения', source_link)
                                    for gap in missing_facets(expanded, part):
                                        content += '<p>Недостаточно данных для части: ' + esc(gap) + '.</p>'
                                else:
                                    content += '<h4>Подтверждённые пункты; порядок выполнения уточните по источнику</h4><ol>'
                                    for item in checked_result['items']:
                                        content += '<li>' + esc(item['text']) + source_link(item['basis']) + '</li>'
                                    content += '</ol>'
                            for item in checked_result['limits']:
                                content += '<p>Ограничение: ' + esc(item['text']) + source_link(item['basis']) + '</p>'
                            for item in checked_result['questions']:
                                content += '<p>' + esc(item) + '</p>'
                            for item in checked_result['conflicts']:
                                content += '<p>Обнаружено противоречие оснований; применимый вариант не установлен: ' + esc(', '.join(item['rules'])) + source_link(item['basis']) + '</p>'
                        return self.page(content)
                try:
                    from .evidence import query
                    result = query(db, question, fields.get('date',[''])[0] or None, {'категория': fields.get('category',[''])[0]})
                except sqlite3.OperationalError as exc:
                    if 'no such table: evidence_topics' not in str(exc): raise
                    result = None
                if result is None:
                    generator = None
                    if os.environ.get('NAVIGATOR_MODEL'):
                        from .local_model import answer as local_answer
                        generator = lambda rows: local_answer(question, rows, os.environ['NAVIGATOR_MODEL'])
                    when = fields.get('date', [''])[0] or None
                    if when:
                        from .evidence import iso
                        iso(when)  # malformed date → ValueError → handled as a bad request below
                    status, answer = form_answer(db, question, generator, when)
                    version_notes = []
                    has_imports = bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='import_fragments'").fetchone())
                    # Notes on excluded versions also explain an "insufficient" result.
                    staged_rows, missing_parts, _ = imported_parts(db, question, when, version_notes) if has_imports and status in ('imported', 'imported_partial', 'insufficient_procedure', 'insufficient') else ([], [], False)
                    if status == 'support':
                        from .procedures import source_limits
                        cited = []
                        for source in answer['sources']:
                            for units in source['facets'].values():
                                cited.extend(dict(u, source_id=source['source_id'], version=source['version']) for u in units)
                        source_notes = source_limits(db, cited)
                        import json
                        import_meta = {}
                        for unit in cited + [i for e in answer['extra'] for i in e['items']]:
                            r = db.execute('SELECT metadata FROM import_fragments WHERE id=?', (unit['id'],)).fetchone()
                            if r: import_meta[unit['id']] = json.loads(r['metadata'])
                    else:
                        import_meta = {}  # (was reset unconditionally, dropping OCR notes of support citations)
                    cited_labels = []
                    if status in ('imported', 'imported_partial'):
                        import json
                        for _, fid in answer:
                            r = db.execute('SELECT metadata FROM import_fragments WHERE id=?', (fid,)).fetchone()
                            if r: import_meta[fid] = json.loads(r['metadata'])
                        # Limitations stated by the cited versions themselves.
                        from .procedures import source_limits
                        cited = []
                        for text, fid in answer:
                            r = db.execute('SELECT source_id, version FROM import_fragments WHERE id=?', (fid,)).fetchone()
                            if r: cited.append({'id': fid, 'text': text, 'source_id': r['source_id'], 'version': r['version']})
                        source_notes = source_limits(db, cited)
                        # Requisites of the cited versions as stated in their texts.
                        from .requisites import requisites as doc_requisites, label as requisites_label
                        cited_labels = []
                        for key in dict.fromkeys((c['source_id'], c['version']) for c in cited):
                            name = db.execute('SELECT filename FROM imports WHERE source_id=? AND version=?', key).fetchone()
                            cited_labels.append((name['filename'] if name else '', requisites_label(doc_requisites(db, *key))))
                else:
                    content = self.form(question) + '<h2>Подтверждённые сведения</h2>'
                    def link(fid):
                        return ' <a href="/fragment?id=' + quote(fid, safe='') + '">' + esc(fid) + '</a>' if fid else ''
                    for item in result['items']:
                        content += '<p>' + esc(item['text']) + link(item['basis']) + '</p>'
                    for item in result['limits']:
                        content += '<p>Ограничение: ' + esc(item['text']) + link(item['basis']) + '</p>'
                    for item in result['questions']:
                        content += '<p>' + esc(item) + '</p>'
                    for item in result['conflicts']:
                        content += '<p>Обнаружено противоречие оснований; применимый вариант не установлен: ' + esc(', '.join(item['rules'])) + link(item['basis']) + '</p>'
                    return self.page(content)
            content = self.form(question)
            def version_block():
                return ''.join('<p>Ограничение: ' + esc(note) + (' ' + cite(fid) if fid else '') + '</p>'
                               for note, fid in version_notes)
            if status == 'insufficient':
                content += '<p>Недостаточно данных для ответа в доступных материалах.</p>' + version_block()
            elif status == 'insufficient_procedure':
                content += '<p>Недостаточно данных для частей вопроса: ' + esc('; '.join(missing_parts)) + '.</p>'
            elif status == 'insufficient_context':
                content += '<p>Недостаточно контекста для вывода: фрагмент слишком длинный для безопасного показа целиком.</p>'
            elif status == 'support':
                content += render_support(answer, import_meta, source_notes)
            elif status in ('imported', 'imported_partial'):
                if status == 'imported_partial':
                    content += '<p>Недостаточно данных для части вопроса: ' + esc('; '.join(missing_parts)) + '.</p>'
                content += '<h2>Найденные фрагменты</h2>'
                if cited_labels and all(text for _, text in cited_labels):
                    content += ('<p>Применимость и полнота проверены только по реквизитам и ссылкам в тексте импортированных '
                                'документов' + (' на ' + esc(when) if when else ' на текущую дату') + '; сверьте источник.</p>')
                else:
                    content += '<p>Применимость и полнота не проверены; дата вступления и связь редакций не подтверждены. Выбранная дата не определяет применимую редакцию. Сверьте источник.</p>'
                for name, text in cited_labels:
                    if text:
                        content += '<p>Источник: ' + esc((name + ' — ') if name else '') + esc(text) + ' (по тексту документа).</p>'
                content += version_block()
                from .procedures import staged, stage, CONDITION
                def import_link(fragment_id, item=None):
                    return ''.join(' <em>' + esc(note) + '</em>' for note in import_notes(import_meta.get(fragment_id, {}))) + cite(fragment_id, item)
                if staged(question):
                    unit_of = {(r['text'], r['id']): r for r in staged_rows if isinstance(r, dict)}
                    items = [{'text': text, 'basis': fid, 'stage': unit_of.get((text, fid), {}).get('stage', stage(text)),
                              'condition': bool(CONDITION.search(text)),
                              'start': unit_of.get((text, fid), {}).get('start'), 'end': unit_of.get((text, fid), {}).get('end')}
                             for text, fid in answer]
                    content += render_stages(items, 'Другие сведения', import_link)
                else:
                    content += '<ol>'
                    for text, fragment_id in answer:
                        content += '<li>' + esc(text) + import_link(fragment_id) + '</li>'
                    content += '</ol>'
                for note, fid in source_notes:
                    content += '<p>Ограничение: ' + esc(note) + ' <a href="/fragment?id=' + quote(fid, safe='') + '">' + esc(fid) + '</a></p>'
            else:
                content += '<h2>Ответ и порядок действий</h2><p>Только вымышленный учебный пример.</p>' + ('<p>Частичный ответ: проверьте приведённые основания.</p>' if os.environ.get('NAVIGATOR_MODEL') else '') + '<ol>'
                for text, fragment_id in answer:
                    content += '<li>' + esc(text) + ' <a href="/fragment?id=' + quote(fragment_id, safe='') + '">' + esc(fragment_id) + '</a></li>'
                content += '</ol>'
            return self.page(content)
        except (sqlite3.Error, OSError, UnicodeError, ValueError):
            return self.page(self.form() + '<p>Технический сбой. Повторите попытку.</p>', 503)
        except Exception as exc:
            from .local_model import ModelError
            if not isinstance(exc, ModelError): raise
            return self.page(self.form(question) + '<p>Ошибка локальной модели или недопустимый результат. Повторите попытку.</p>', 503)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', nargs='?', choices=['serve', 'import-fixture', 'import', 'audit', 'load-evidence', 'embed'], default='serve')
    parser.add_argument('paths', nargs='*')
    parser.add_argument('--manifest', default='sdd/sources/manifest.json')
    parser.add_argument('--report', help='Local JSON report path')
    args = parser.parse_args()
    if args.command == 'import-fixture':
        import_fixture(DB)
        print('Synthetic fixture imported')
    elif args.command == 'load-evidence':
        import json
        from pathlib import Path
        from .evidence import load
        if len(args.paths) != 1: parser.error('load-evidence requires one JSON file')
        Path(DB).parent.mkdir(parents=True, exist_ok=True)
        from .importer import init
        with closing(connect(DB)) as db, db:
            init(db)
            load(db, json.loads(Path(args.paths[0]).read_text(encoding='utf-8')))
        print('Evidence catalogue loaded')
    elif args.command == 'embed':
        # Fill the local vector cache for dense/hybrid retrieval; texts stay local.
        from . import dense
        enc = dense.encoder()
        if enc is None:
            raise SystemExit('Dense model unavailable: ' + dense.status().get('fallback', 'set NAVIGATOR_EMBED_MODEL'))
        with closing(connect(DB)) as db:
            print(f'Embedded fragments: {dense.ensure_vectors(db, enc)} (model {enc.name})')
    elif args.command in ('import', 'audit'):
        import json
        from .importer import batch, audit
        if args.command == 'import' and not args.paths:
            parser.error('import requires files or ZIP archives')
        result = batch(DB, args.paths, args.manifest) if args.command == 'import' else audit(DB, args.manifest)
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        if args.report:
            from pathlib import Path
            report = Path(args.report)
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(rendered + '\n', encoding='utf-8')
            print('Report saved locally')
        else:
            print(rendered)
        if args.command == 'import' and result.get('error', 0):
            raise SystemExit(1)
    else:
        # NAVIGATOR_IMPORT_DIR: import the documents of that folder first (the
        # container sets /data/originals), then serve.
        from .autoimport import from_environment
        from_environment(DB, log=lambda message: print(message, flush=True))
        HTTPServer((os.environ.get('HOST', '127.0.0.1'), int(os.environ.get('PORT', '8000'))), Handler).serve_forever()
