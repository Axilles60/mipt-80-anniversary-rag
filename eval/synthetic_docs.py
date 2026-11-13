"""Builders for fictional regulations used by diagnostics and web tests.

All texts, form identifiers, addresses and deadlines are invented. They model
layout problems of real corpora (page-level PDF fragments, headings that carry
the procedure stage, conditions on a neighbouring page), not their content.
"""
import html
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def write_pdf(path, pages):
    """pages: list of lists of paragraphs; each inner list is one PDF page."""
    try:
        import pymupdf as fitz
    except ImportError:  # older PyMuPDF
        import fitz
    doc = fitz.open()
    for paragraphs in pages:
        page = doc.new_page()
        body = ''.join('<p>' + html.escape(p) + '</p>' for p in paragraphs)
        page.insert_htmlbox(fitz.Rect(40, 40, page.rect.width - 40, page.rect.height - 40), body,
                            css='p{font-size:10pt;margin:0 0 4pt 0}')
    doc.save(str(path))
    doc.close()
    return Path(path)


def write_docx(path, paragraphs):
    """paragraphs: strings, or (style, text) tuples for headings."""
    body = ''
    for item in paragraphs:
        style, text = item if isinstance(item, tuple) else (None, item)
        ppr = '<w:pPr><w:pStyle w:val="' + style + '"/></w:pPr>' if style else ''
        body += '<w:p>' + ppr + '<w:r><w:t xml:space="preserve">' + escape(text) + '</w:t></w:r></w:p>'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('word/document.xml', '<w:document xmlns:w="' + W_NS + '"><w:body>' + body + '</w:body></w:document>')
    return Path(path)


# A fictional travel regulation. The action, the form, the deadline and the
# condition of each stage are deliberately placed in different paragraphs and
# on different PDF pages; most sentences do not repeat the stage word, which is
# carried by the section heading. Pages also contain unrelated uses of "до".
TRIP_PAGES = [
    ['ПОЛОЖЕНИЕ о служебных поездках (учебный вымышленный документ)',
     'Утверждено приказом от «__» ________ 20__ г. № ____',
     '1. Общие положения',
     '1.1. Положение применяется до утверждения новой редакции.',
     '1.2. Служебная поездка оформляется решением руководителя подразделения.',
     '2. Оформление до начала поездки',
     '2.1. Работник направляет в отдел поездок служебную записку.'],
    ['2.2. Служебная записка составляется по форме ФП-11 (приложение 1).',
     '2.3. Записку представляют не позднее шести рабочих дней до даты выезда.',
     '2.4. Если поездка однодневная, записку допускается представить в день поездки.',
     '3. Расходы',
     '3.1. Суточные выплачиваются за каждый день, включая время в пути, до 700 условных единиц.'],
    ['4. Действия по возвращении',
     '4.1. Работник представляет авансовый отчёт в бухгалтерию.',
     '4.2. Авансовый отчёт заполняется по ф. ФП-52 (приложение 2).',
     '4.3. Срок представления отчёта — три рабочих дня со дня возвращения.',
     '4.4. Если поездка длилась более десяти календарных дней, срок увеличивается до пяти рабочих дней.',
     'Приложения: 1 — форма ФП-11; 2 — форма ФП-52.'],
]

# Facts a correct answer must keep, with the stage they belong to. Matching is
# by an exact, distinctive substring of the source paragraph.
TRIP_FACTS = [
    ('до', 'действие', 'направляет в отдел поездок служебную записку'),
    ('до', 'номер формы', 'по форме ФП-11'),
    ('до', 'срок', 'не позднее шести рабочих дней до даты выезда'),
    ('до', 'условие', 'Если поездка однодневная'),
    ('после', 'действие', 'представляет авансовый отчёт в бухгалтерию'),
    ('после', 'номер формы', 'по ф. ФП-52'),
    ('после', 'срок', 'три рабочих дня со дня возвращения'),
    ('после', 'условие', 'Если поездка длилась более десяти календарных дней'),
]

# Competing fictional documents with stage words and similar vocabulary.
TRIP_COMPETITORS = {
    'transport.pdf': [[
        'Порядок использования служебного транспорта (вымышленный)',
        'До выезда водитель проверяет путевой лист по форме ТР-3.',
        'После возвращения автомобиль ставят на стоянку в течение одного часа.']],
    'planning.pdf': [[
        'Планирование расходов на поездки (вымышленное)',
        'Перед началом года финансист составляет смету поездок.',
        'После утверждения сметы её направляют руководителю.']],
}


def trip_docx_paragraphs(pages=TRIP_PAGES):
    out = []
    for page in pages:
        for text in page:
            heading = text[:2] in ('1.', '2.', '3.', '4.') and text[2:3] == ' '
            out.append(('Heading1', text) if heading else text)
    return out


# A fictional first-line support regulation laid out over three PDF pages. The
# channel, the request content and the processing time are on different pages.
SUPPORT_PAGES = [
    ['РЕГЛАМЕНТ первой линии технической поддержки (учебный вымышленный документ)',
     '1. Общие положения',
     '1.1. Первая линия является единой точкой приёма обращений о сбоях оборудования и программ.',
     '2. Каналы обращения',
     '2.1. Обращение регистрируется через портал поддержки portal.example.invalid/support.',
     '2.2. Резервный канал — электронная почта support@example.invalid.',
     '2.3. Звонки и сообщения в мессенджерах обращениями не считаются.'],
    ['3. Состав обращения',
     '3.1. В обращении указываются ФИО, телефон, кабинет, подразделение и описание проблемы.',
     '3.2. Для неисправного устройства дополнительно указывается инвентарный номер вида PC-0000.',
     '3.3. Устройства, не числящиеся на балансе организации, не обслуживаются.'],
    ['4. Сроки',
     '4.1. Первичная обработка обращения выполняется не более 16 рабочих часов.',
     '4.2. Срок первичной обработки не является сроком устранения неисправности.',
     '4.3. Если автор не отвечает на запрос сведений 3 рабочих дня, обращение закрывается.'],
]
SUPPORT_FACTS = {
    'channel': 'через портал поддержки portal.example.invalid/support',
    'details': 'указываются ФИО, телефон, кабинет, подразделение и описание проблемы',
    'inventory': 'инвентарный номер вида PC-0000',
    'processing': 'Первичная обработка обращения выполняется не более 16 рабочих часов',
    'not_repair': 'не является сроком устранения неисправности',
}
# A budget table with a repair line and similar vocabulary.
SUPPORT_BUDGET = [
    ['Статья', 'Описание', 'Сумма'],
    ['Ремонт оргтехники', 'Ремонт неисправных принтеров и МФУ подрядчиком', 120000],
    ['Расходные материалы', 'Картриджи для принтеров по заявкам подразделений', 45000],
]
# A second, narrower fictional regulation that is also relevant to printers.
PRINT_SERVICE_PAGES = [
    ['ПОРЯДОК обслуживания печатной техники (учебный вымышленный документ)',
     '1. При неисправности принтера или МФУ сотрудник подаёт заявку через портал печати print.example.invalid.',
     '2. В заявке указываются кабинет, модель устройства и описание неисправности.',
     '3. Заявка на замену картриджа подаётся через тот же портал с указанием модели картриджа.',
     '4. Заявка о неисправности рассматривается в течение 2 рабочих дней.'],
]
# Similar vocabulary, different subject.
PURCHASE_PAGES = [
    ['ПОРЯДОК закупки оборудования (учебный вымышленный документ)',
     '1. Заявка на закупку принтера подаётся через портал закупок buy.example.invalid.',
     '2. В заявке указываются подразделение, количество и обоснование потребности.'],
]


def write_xlsx(path, rows, title='Бюджет'):
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = title
    for row in rows: ws.append(row)
    wb.save(str(path))
    return Path(path)
