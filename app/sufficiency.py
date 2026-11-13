"""Conservative lexical checks for recognised requests in extracted fragments.

A match is not proof of entailment or applicability. In particular, conditions,
complex syntax and references across sentences can require human review.
"""
import re

INTENTS = (
    (r'оплат|оплач|платит|платеж|возмещ|компенсир', r'оплат|оплач|платит|платеж|возмещ|компенсир'),
    (r'отмен|аннулир|отозва|отзыв', r'отмен|аннулир|отозва|отзыв'),
    (r'продл|пролонгир', r'продл|пролонгир'),
    (r'цен[аыуеой]|стоимост|тариф', r'цен[аыуеой]|стоимост|тариф'),
)
PAYMENT = INTENTS[0][0]
DEADLINE = r'\bсрок\w*|сколько\s+времени|за\s+сколько'
DURATION = (r'(?:\d+|одн\w*|дв[аеухум]*|тр[иеехёмя]*|четыр\w*|пят\w*|шест\w*|'
            r'сем\w*|восем\w*|девят\w*|десят\w*)\s+'
            r'(?:рабоч\w*\s+|календарн\w*\s+)?(?:дн\w*|ден\w*|час\w*|недел\w*|месяц\w*|сут\w*)')
NO_PAYMENT = r'\bбесплатн\w*|(?:оплат\w*|плат\w*)[^.;,:]{0,60}\bне\s+(?:требу\w*|взима\w*|нужн\w*|производ\w*)|не\s+оплачива\w*'
PAYER = r'\bоплачива(?:ет|ют|л|ла|ли|ю|ем)\b|\bоплат(?:ит|ят|ил|ила|или)\b|\bплат(?:ит|ят|ил|ила|или)\b|оплат\w*[^.;,:]{0,70}\bосуществля(?:ет|ют|л|ла|ли)\b|за\s+счет\s+\w+'
ACTION_GROUPS = (
    (r'выда\w*|выдают\w*|выдат\w*', r'выда\w*|выдают\w*|выдат\w*'),
    (r'оформ\w*', r'оформ\w*'),
    (r'рассмотр\w*|обработ\w*', r'рассмотр\w*|обработ\w*'),
    (r'достав\w*', r'достав\w*'),
)
STOP = {'кто','что','где','куда','как','какой','какая','какие','каком','какова',
        'когда','каков','каковы','сколько','время','времени','срок','сроки',
        'нужно','нужен','можно','должен','должна','необходимо','при','для','что',
        'какую','каком','порядок','порядке','дней','рабочих','быстро'}

def _stem(word):
    # Limited Russian case endings. This avoids accepting a related adjective
    # such as "пропускной" as the requested object "пропуск".
    for ending in ('ами', 'ями', 'ого', 'ему', 'ому', 'ами', 'ой', 'ей', 'ом', 'ем', 'ах', 'ях', 'ам', 'ям', 'ов', 'ев', 'а', 'я', 'у', 'ю', 'е', 'ы', 'и'):
        if len(word) - len(ending) >= 5 and word.endswith(ending):
            return word[:-len(ending)]
    return word

def _objects(question):
    # Stems provide a limited match across inflections; function words and
    # requested predicates do not identify the object of the fact.
    words = re.findall(r'[а-яa-z0-9]+', question)
    ignored = re.compile(PAYMENT + r'|отмен|аннулир|отозва|отзыв|продл|пролонгир|'
                         r'стоимост|цен[аыуеой]|тариф|срок|выда|выдают|выдат|'
                         r'оформ|рассмотр|обработ|достав')
    return list(dict.fromkeys(_stem(w) for w in words if len(w) >= 4 and w not in STOP and not ignored.search(w)))

def _clauses(text):
    return [part.strip() for part in re.split(r'[.!?;,:]|\s+(?:но|однако|тогда как)\s+', text) if part.strip()]

def _anchored(clause, objects):
    tokens = re.findall(r'[а-яa-z0-9]+', clause)
    return bool(objects) and all(any(_stem(word) == obj for word in tokens) for obj in objects)

def _payment_related(clause, objects):
    words = re.findall(r'[а-яa-z0-9]+', clause)
    markers = [i for i, word in enumerate(words) if re.search(PAYMENT, word) or word.startswith('бесплатн')]
    targets = [i for i, word in enumerate(words) if _stem(word) in objects]
    # Only adjacent object references or a single linking preposition; a
    # different intervening object must not inherit the payment statement.
    for i in markers:
        for j in targets:
            between = words[min(i, j)+1:max(i, j)]
            if not between or (len(between) == 1 and between[0] in {'за', 'для', 'на', 'по'}):
                return True
    return False

def _has_payer(clause, objects):
    if not re.search(PAYER, clause):
        return False
    words = re.findall(r'[а-яa-z]+', clause)
    ignored = re.compile(r'оплат|оплач|платит|платят|осуществля|счет|выда')
    return any(len(word) >= 4 and word not in STOP and
               not ignored.search(word) and _stem(word) not in objects
               for word in words)

def supports(question, text):
    question = question.lower().replace('ё', 'е')
    text = text.lower().replace('ё', 'е')
    objects = _objects(question)
    clauses = _clauses(text)
    for request, evidence in INTENTS:
        if re.search(request, question):
            relevant = [c for c in clauses if (re.search(evidence, c) or
                        (request == PAYMENT and re.search(NO_PAYMENT, c))) and _anchored(c, objects)]
            if request == PAYMENT:
                relevant = [c for c in relevant if _payment_related(c, objects)]
            # "Who pays?" requires a payer, or an explicit waiver. A mention
            # of payment being accepted is not itself evidence of a payer.
            if request == PAYMENT and re.search(r'\bкто\b', question):
                relevant = [c for c in relevant if _has_payer(c, objects) or re.search(NO_PAYMENT, c)]
            if not relevant:
                return False
    if re.search(DEADLINE, question):
        actions = [evidence for request, evidence in ACTION_GROUPS if re.search(request, question)]
        intents = [evidence for request, evidence in INTENTS if re.search(request, question)]
        if not any(_anchored(c, objects) and (duration := re.search(DURATION, c)) and
                   all((match := re.search(action, c)) and match.start() < duration.start()
                       for action in actions + intents) and
                   (not re.search(PAYMENT, question) or _payment_related(c, objects))
                   for c in clauses):
            return False
    return True
