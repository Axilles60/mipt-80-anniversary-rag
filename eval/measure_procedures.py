"""Synthetic end-to-end measurement of staged procedures and fault reports.

Usage from the project root:
    python eval/measure_procedures.py [--split dev|holdout|all] [--report PATH]

Each case builds fictional documents, imports them with the standard CLI into
a new temporary database, sends POST /ask on loopback and opens every cited
link. Metrics per split:
- correct: expected status, all required facts with an adjacent link to the
  fragment that contains them, the cited item highlighted, no forbidden text;
- false_answers: forbidden text shown, or an answer where a refusal was expected,
  or a partial answer shown without naming the gap;
- missing_facts: required facts absent from the answer;
- citation_errors: a required fact without a link to its own fragment, a link
  that does not open, or a cited item that is not highlighted;
- correct_refusals: expected refusal given without any answer item.
The holdout split was written before any change motivated by it and is not
used for tuning; results are recorded separately in sdd/quality-progress.md.
"""
import argparse
import html
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'eval'))
from synthetic_docs import (PRINT_SERVICE_PAGES, PURCHASE_PAGES, SUPPORT_BUDGET, SUPPORT_PAGES, TRIP_COMPETITORS,  # noqa: E402
                            TRIP_PAGES, trip_docx_paragraphs, write_docx, write_pdf, write_xlsx)
from web_harness import Session  # noqa: E402

FILLER = ('Настоящий раздел носит справочный характер и описывает общие принципы работы подразделений, '
          'порядок взаимодействия и распределение ответственности между участниками процесса.')


def docx_table(path, paragraphs, rows):
    """DOCX with paragraphs followed by one table."""
    from xml.sax.saxutils import escape
    ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    body = ''.join('<w:p><w:r><w:t xml:space="preserve">' + escape(p) + '</w:t></w:r></w:p>' for p in paragraphs)
    body += '<w:tbl>' + ''.join('<w:tr>' + ''.join('<w:tc><w:p><w:r><w:t>' + escape(c) + '</w:t></w:r></w:p></w:tc>' for c in row) + '</w:tr>' for row in rows) + '</w:tbl>'
    import zipfile
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('word/document.xml', '<w:document xmlns:w="' + ns + '"><w:body>' + body + '</w:body></w:document>')
    return path


# ---------------------------------------------------------------- corpora
def c_trip_pdf(root):
    return [write_pdf(root / 'trip.pdf', TRIP_PAGES)] + [write_pdf(root / n, p) for n, p in TRIP_COMPETITORS.items()]

def c_trip_docx(root):
    return [write_docx(root / 'trip.docx', trip_docx_paragraphs())] + [write_pdf(root / n, p) for n, p in TRIP_COMPETITORS.items()]

def c_support(root):
    return [write_pdf(root / 'support.pdf', SUPPORT_PAGES), write_xlsx(root / 'budget.xlsx', SUPPORT_BUDGET),
            write_pdf(root / 'purchase.pdf', PURCHASE_PAGES)]

def c_support_two(root):
    return c_support(root) + [write_pdf(root / 'print.pdf', PRINT_SERVICE_PAGES)]

TRAINING_LONG = [
    ['ПОЛОЖЕНИЕ о повышении квалификации (учебный вымышленный документ)'] + [FILLER] * 6 +
    ['5. Порядок направления на обучение',
     '5.1. До начала обучения работник подаёт заявку по форме ПК-2 руководителю подразделения.',
     '5.2. Заявку подают не позднее 10 календарных дней до начала обучения.'] + [FILLER] * 5,
    [FILLER] * 4 +
    ['6. Завершение обучения',
     '6.1. По окончании обучения работник передаёт копию удостоверения в отдел кадров.',
     '6.2. Копию передают в течение 5 рабочих дней по форме ПК-7.',
     '6.3. Если обучение прервано, работник уведомляет отдел кадров в течение 2 рабочих дней.'] + [FILLER] * 4,
]

def c_training_long(root):
    return [write_pdf(root / 'training.pdf', TRAINING_LONG)] + [write_pdf(root / n, p) for n, p in TRIP_COMPETITORS.items()]

TRIP_EXCEPTION = [
    ['ПОРЯДОК служебных выездов (учебный вымышленный документ)',
     '1. Подготовка выезда',
     '1.1. Сотрудник представляет заявление по форме СВ-1 не позднее 5 рабочих дней до выезда.',
     '1.2. Указанный срок не применяется к выездам по срочному вызову руководителя: заявление подаётся в день выезда.',
     '2. После возвращения',
     '2.1. Сотрудник сдаёт отчёт по форме СВ-4 в течение 3 рабочих дней.']]

def c_trip_exception(root):
    return [write_pdf(root / 'trips.pdf', TRIP_EXCEPTION)]

def c_trip_table(root):
    return [docx_table(root / 'trip-table.docx', ['ТАБЛИЦА этапов служебной поездки (учебный вымышленный документ)'],
                       [['Этап', 'Действие', 'Срок', 'Форма'],
                        ['До поездки', 'Подать заявление в отдел поездок', 'за 5 рабочих дней', 'ФК-1'],
                        ['После возвращения', 'Сдать авансовый отчёт в бухгалтерию', 'в течение 3 рабочих дней', 'ФК-9']])]

def c_support_table(root):
    return [docx_table(root / 'it.docx', ['ПАМЯТКА по обращениям о неисправностях (учебный вымышленный документ)',
                                          'О неисправности оборудования сотрудник сообщает по одному из каналов из таблицы.',
                                          'В обращении указываются кабинет, телефон и описание неисправности.'],
                       [['Канал', 'Адрес'], ['Портал', 'it.example.invalid'], ['Почта', 'it@example.invalid']]),
            docx_table(root / 'buy.docx', ['ПАМЯТКА по закупкам (учебный вымышленный документ)',
                                           'Заявки на закупку подаются по каналам из таблицы.'],
                       [['Канал', 'Адрес'], ['Портал закупок', 'buy.example.invalid']])]

def c_hr_only(root):
    return [write_pdf(root / 'hr.pdf', [['ПОРЯДОК оформления отпуска (учебный вымышленный документ)',
                                         '1. Заявление на отпуск подаётся через портал кадров hr.example.invalid.',
                                         '2. В заявлении указываются даты и подразделение.']])]

def c_support_versions(root):
    first = root / 'v1'; second = root / 'v2'; first.mkdir(); second.mkdir()
    changed = [list(p) for p in SUPPORT_PAGES]
    changed[0][4] = '2.1. Обращение регистрируется через портал поддержки portal2.example.invalid.'
    return [write_pdf(first / 'support.pdf', SUPPORT_PAGES), write_pdf(second / 'support.pdf', changed)]

ATTESTATION = [['ПОРЯДОК аттестации работников (учебный вымышленный документ)',
                '1. Перед аттестацией работник заполняет анкету по форме АТ-3.',
                '2. Анкету передают секретарю комиссии за 7 рабочих дней.',
                '3. После аттестации работник знакомится с протоколом под подпись.',
                '4. Ознакомление проводится в течение 3 рабочих дней.',
                '5. Работник вправе обжаловать решение комиссии.']]

def c_attestation(root):
    return [write_pdf(root / 'attestation.pdf', ATTESTATION)] + [write_pdf(root / n, p) for n, p in TRIP_COMPETITORS.items()]

def c_trip_versions(root):
    first = root / 'v1'; second = root / 'v2'; first.mkdir(); second.mkdir()
    changed = [list(p) for p in TRIP_PAGES]
    changed[1][1] = '2.3. Записку представляют не позднее восьми рабочих дней до даты выезда.'
    return [write_pdf(first / 'trip.pdf', TRIP_PAGES), write_pdf(second / 'trip.pdf', changed)]

STUDENT_TRIPS = [['ПОРЯДОК поездок обучающихся на мероприятия (учебный вымышленный документ)',
                  '1. Подготовка поездки',
                  '1.1. До выезда обучающийся подаёт заявку по форме СТ-5 в деканат.',
                  '1.2. Заявку подают за 10 рабочих дней.',
                  '2. По возвращении',
                  '2.1. Обучающийся сдаёт отчёт о мероприятии по форме СТ-8 в течение 5 рабочих дней.']]

def c_two_trip_docs(root):
    return [write_pdf(root / 'trip.pdf', TRIP_PAGES), write_pdf(root / 'students.pdf', STUDENT_TRIPS)]

PASS_PAGES = [['ПОРЯДОК выдачи пропусков (учебный вымышленный документ)',
               '1. Временный пропуск выдаёт бюро пропусков по заявлению руководителя подразделения.',
               '2. Пропуск выдаётся в течение 1 рабочего дня.']]

def c_support_pass(root):
    return c_support(root) + [write_pdf(root / 'pass.pdf', PASS_PAGES)]

def c_exception_docx(root):
    return [write_docx(root / 'leave.docx', [
        ('Heading1', 'Оформление до начала поездки'),
        'Сотрудник направляет служебную записку по форме КМ-2 за 4 рабочих дня.',
        'Исключение составляют поездки в выходные дни: записка подаётся за 6 рабочих дней.',
        ('Heading1', 'Действия после возвращения'),
        'Сотрудник представляет отчёт по форме КМ-6 в течение 2 рабочих дней.'])]

CORPORA = {name[2:]: fn for name, fn in globals().items() if name.startswith('c_')}

# ---------------------------------------------------------------- cases
# facts: (text, heading substring or None); forbidden: texts that must not appear.
DEV = [
    dict(id='D01', corpus='trip_pdf', question='Какие формы и сроки нужны до и после командировки?', status='answer',
         facts=[('по форме ФП-11', 'До'), ('по ф. ФП-52', 'После'), ('три рабочих дня со дня возвращения', 'После')],
         forbidden=['до 700 условных единиц', 'путевой лист']),
    dict(id='D02', corpus='trip_docx', question='Что оформить перед поездкой и что сдать после возвращения из командировки?', status='answer',
         facts=[('по форме ФП-11', 'До'), ('по ф. ФП-52', 'После')], forbidden=['смету поездок']),
    dict(id='D03', corpus='support', question='Сломалось МФУ — куда подать заявку и какие данные указать?', status='answer',
         facts=[('portal.example.invalid/support', 'Куда'), ('описание проблемы', 'Что указать')],
         forbidden=['Ремонт неисправных принтеров', 'портал закупок']),
    dict(id='D04', corpus='support', question='Через сколько починят принтер?', status='partial',
         facts=[('не более 16 рабочих часов', None), ('не является сроком устранения', None)], forbidden=['подрядчиком']),
    dict(id='D05', corpus='support_two', question='Не работает принтер: куда обратиться и как заказать новый картридж?', status='answer',
         facts=[('portal.example.invalid/support', None), ('print.example.invalid', None), ('замену картриджа', None)],
         forbidden=['портал закупок']),
]
HOLDOUT = [
    dict(id='P01', corpus='training_long', question='Что нужно сделать до начала обучения и после его окончания?', status='answer',
         facts=[('по форме ПК-2', 'До'), ('не позднее 10 календарных дней', 'До'), ('копию удостоверения', 'После'),
                ('по форме ПК-7', 'После'), ('Если обучение прервано', 'После')],
         forbidden=['путевой лист', 'смету поездок']),
    dict(id='P02', corpus='trip_exception', question='Когда подать заявление на выезд и что сдать после возвращения?', status='answer',
         facts=[('не позднее 5 рабочих дней до выезда', 'До'), ('по срочному вызову руководителя', 'До'),
                ('по форме СВ-4', 'После')], forbidden=[]),
    dict(id='P03', corpus='trip_table', question='Какие формы и сроки до поездки и после возвращения?', status='answer',
         facts=[('ФК-1', 'До'), ('за 5 рабочих дней', 'До'), ('ФК-9', 'После'), ('в течение 3 рабочих дней', 'После')], forbidden=[]),
    dict(id='P04', corpus='trip_pdf', question='Что сдать по возвращении из командировки и что оформить заранее?', status='answer',
         facts=[('по ф. ФП-52', None), ('по форме ФП-11', None)], forbidden=['путевой лист', 'смету поездок']),
    dict(id='P05', corpus='support_table', question='Не работает сканер, куда обратиться?', status='answer',
         facts=[('it.example.invalid', None)], forbidden=['buy.example.invalid']),
    dict(id='P06', corpus='hr_only', question='Не работает принтер, куда обратиться?', status='insufficient',
         facts=[], forbidden=['hr.example.invalid']),
    dict(id='P07', corpus='support_versions', question='Сломался компьютер: куда подать обращение?', status='insufficient',
         facts=[], forbidden=['portal.example.invalid/support', 'portal2.example.invalid']),
    dict(id='P08', corpus='support', question='Сломался ноутбук: куда обратиться и сколько ждать ответа?', status='answer',
         facts=[('portal.example.invalid/support', None), ('не более 16 рабочих часов', None)], forbidden=['подрядчиком']),
    dict(id='P09', corpus='support', question='Куда сообщить о сбое в работе почты и как получить новый пропуск?', status='partial',
         facts=[('portal.example.invalid/support', None)], forbidden=['подрядчиком']),
]

# Written after the fixes motivated by HOLDOUT (v1) and before any change for it.
HOLDOUT2 = [
    dict(id='Q01', corpus='attestation', question='Что подготовить перед аттестацией и что делать после неё?', status='answer',
         facts=[('по форме АТ-3', 'До'), ('за 7 рабочих дней', 'До'), ('знакомится с протоколом', 'После'),
                ('в течение 3 рабочих дней', 'После')], forbidden=['путевой лист', 'смету поездок']),
    dict(id='Q02', corpus='trip_versions', question='Какие формы и сроки нужны до и после командировки?', status='insufficient',
         facts=[], forbidden=['ФП-52', 'восьми рабочих дней', 'шести рабочих дней']),
    dict(id='Q03', corpus='support', question='Не работает VPN, куда писать?', status='answer',
         facts=[('portal.example.invalid/support', None)], forbidden=['подрядчиком', 'портал закупок']),
    dict(id='Q04', corpus='two_trip_docs', question='Служебная командировка работника: что сделать до выезда и после возвращения?', status='answer',
         facts=[('по форме ФП-11', 'До'), ('по ф. ФП-52', 'После')], forbidden=['СТ-5', 'СТ-8']),
    dict(id='Q05', corpus='trip_pdf', question='Что нужно сделать до увольнения и после него?', status='insufficient',
         facts=[], forbidden=['ФП-11', 'ФП-52', 'путевой лист']),
    dict(id='Q06', corpus='support_pass', question='Не работает компьютер — куда писать, и как получить временный пропуск?', status='answer',
         facts=[('portal.example.invalid/support', None), ('Временный пропуск выдаёт бюро пропусков', None)],
         forbidden=['подрядчиком']),
    dict(id='Q07', corpus='exception_docx', question='Что оформить до поездки и после возвращения?', status='answer',
         facts=[('по форме КМ-2', 'До'), ('Исключение составляют поездки в выходные дни', 'До'), ('по форме КМ-6', 'После')],
         forbidden=[]),
]


ONBOARDING = [['ПОРЯДОК приёма на работу (учебный вымышленный документ)',
               '1. Общие положения',
               '1.1. Порядок применяется ко всем подразделениям.',
               '2. До выхода на работу',
               '2.1. Кандидат представляет в отдел кадров заявление по форме ПР-1.',
               '2.2. Документы передаются не позднее трёх рабочих дней до первого рабочего дня.'],
              ['3. После оформления',
               '3.1. Работник проходит вводный инструктаж у руководителя подразделения.',
               '3.2. Пропуск выдаётся в течение двух рабочих дней после подписания договора.']]

def c_onboarding(root):
    return [write_pdf(root / 'onboarding.pdf', ONBOARDING)] + [write_pdf(root / n, p) for n, p in TRIP_COMPETITORS.items()]

def c_repair_table(root):
    path = docx_table(root / 'repair.docx', ['ПОРЯДОК ремонта служебных помещений (учебный вымышленный документ)'],
                      [['Этап', 'Действие', 'Срок'],
                       ['До начала ремонта', 'Подать заявку по форме РМ-1 в хозяйственный отдел', 'за 10 рабочих дней'],
                       ['После завершения ремонта', 'Подписать акт приёмки по форме РМ-4', 'в течение 2 рабочих дней']])
    return [path, write_xlsx(root / 'budget.xlsx', SUPPORT_BUDGET)] + [write_pdf(root / n, p) for n, p in TRIP_COMPETITORS.items()]

CORPORA.update(onboarding=c_onboarding, repair_table=c_repair_table)

# Written after iteration 13 (stages from the question's event) and before any
# change for it; run once. HOLDOUT2 is a tuning split from iteration 13 on.
HOLDOUT3 = [
    dict(id='R01', corpus='onboarding', question='Что сделать до выхода на работу и после оформления?', status='answer',
         facts=[('по форме ПР-1', 'До'), ('не позднее трёх рабочих дней', 'До'), ('вводный инструктаж', 'После'),
                ('в течение двух рабочих дней', 'После')], forbidden=['путевой лист', 'смету поездок']),
    dict(id='R02', corpus='onboarding', question='Что сделать до отпуска и после него?', status='insufficient',
         facts=[], forbidden=['ПР-1']),
    dict(id='R03', corpus='onboarding', question='Что нужно сделать перед аттестацией и после неё?', status='insufficient',
         facts=[], forbidden=['ПР-1', 'путевой лист']),
    dict(id='R04', corpus='repair_table', question='Что сделать до начала ремонта кабинета и после его завершения?', status='answer',
         facts=[('по форме РМ-1', 'До'), ('за 10 рабочих дней', 'До'), ('по форме РМ-4', 'После')],
         forbidden=['подрядчиком', 'путевой лист']),
    dict(id='R05', corpus='trip_docx', question='Командировка: какие документы оформить заранее и что сдать по возвращении?',
         status='answer', facts=[('по форме ФП-11', 'До'), ('по ф. ФП-52', 'После')], forbidden=['путевой лист']),
]


def answer_items(items):
    skip = ('Ограничение:', 'Обнаружено противоречие', 'Недостаточно', 'Применимость и полнота', 'Укажите дату', 'Уточните условие')
    return [i for i in items if not i['text'].strip().startswith(skip)]


def run_case(case, session, fragments):
    page, items = session.ask(case['question'], case.get('date', ''))
    answer = answer_items(items)
    gaps = [i for i in items if 'Недостаточно' in i['text']]
    status = 'partial' if answer and gaps else 'answer' if answer else 'insufficient' if gaps else 'other'
    text = ' '.join(i['text'] for i in answer)
    forbidden = [f for f in case['forbidden'] if f.lower() in page.lower()]
    missing, errors = [], []
    for needle, heading in case['facts']:
        holder = next((f for f in fragments if needle in re.sub(r'\s+', ' ', f['text'])), None)
        shown = [i for i in answer if needle in i['text']]
        if not shown:
            missing.append(needle); continue
        linked = [i for i in shown if holder and holder['id'] in i['links']]
        if not linked:
            errors.append({'type': 'fact_without_own_citation', 'fact': needle}); continue
        item = linked[0]
        if heading and not any(heading.lower() in h.lower() for h in item['headings'].values()):
            errors.append({'type': 'wrong_section', 'fact': needle, 'headings': item['headings']})
        try:
            source = re.sub(r'\s+', ' ', html.unescape(session.open(item['hrefs'][item['links'].index(holder['id'])])))
            if needle not in source: raise ValueError('fact not in opened fragment')
            mark = re.search(r'<mark id="cited">(.*?)</mark>', source)
            if mark is None and len(holder['text']) > 4 * len(item['text']):
                errors.append({'type': 'not_highlighted', 'fact': needle})
            elif mark is not None and needle not in mark.group(1):
                errors.append({'type': 'wrong_highlight', 'fact': needle})
        except Exception as exc:
            errors.append({'type': 'unopenable', 'fact': needle, 'error': str(exc)})
    for item in answer:
        if not item['links']:
            errors.append({'type': 'uncited_answer', 'text': item['text'][:120]})
    false = bool(forbidden or (case['status'] == 'insufficient' and answer) or (case['status'] == 'partial' and status == 'answer'))
    correct = status == case['status'] and not missing and not forbidden and not errors
    return dict(id=case['id'], question=case['question'], expected=case['status'], actual=status, correct=correct,
                false_answer=false, correct_refusal=case['status'] == 'insufficient' and status == 'insufficient',
                missing_facts=missing, forbidden_found=forbidden, citation_errors=errors)


def measure(cases):
    results = []
    by_corpus = {}
    for case in cases: by_corpus.setdefault(case['corpus'], []).append(case)
    for corpus, group in by_corpus.items():
        with Session(CORPORA[corpus]) as session:
            fragments = session.fragments()
            for case in group: results.append(run_case(case, session, fragments))
    results.sort(key=lambda r: r['id'])
    return {'cases': len(results), 'correct': sum(r['correct'] for r in results),
            'false_answers': sum(r['false_answer'] for r in results),
            'missing_facts': sum(len(r['missing_facts']) for r in results),
            'citation_errors': sum(len(r['citation_errors']) for r in results),
            'correct_refusals': sum(r['correct_refusal'] for r in results),
            'expected_refusals': sum(r['expected'] == 'insufficient' for r in results),
            'results': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--split', choices=['dev', 'holdout', 'holdout2', 'holdout3', 'all'], default='all')
    parser.add_argument('--report')
    args = parser.parse_args()
    out = {'data': 'synthetic', 'mode': 'extractive'}
    if args.split in ('dev', 'all'): out['dev'] = measure(DEV)
    if args.split in ('holdout', 'all'): out['holdout'] = measure(HOLDOUT)
    if args.split in ('holdout2', 'all'): out['holdout2'] = measure(HOLDOUT2)
    if args.split in ('holdout3', 'all'): out['holdout3'] = measure(HOLDOUT3)
    text = json.dumps(out, ensure_ascii=False, indent=1)
    if args.report:
        report = Path(args.report)
        if report.exists(): raise SystemExit('report exists: ' + str(report))
        report.write_text(text + '\n', encoding='utf-8')
    print(text)
