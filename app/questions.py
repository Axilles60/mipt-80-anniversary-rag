"""Conservative splitting of independent question parts, with shared objects."""
import re

# A coordinated noun phrase is kept whole. Split only when a new interrogative
# clause or an infinitive explicitly introduces another requested action.
CLAUSE = re.compile(r'^(?:как|кто|куда|где|что|когда|сколько|зачем|почему|'
                    r'в\s+како\w*|како\w*|кем|чем|каким\w*)\b', re.I)
INFINITIVE = re.compile(r'^([а-яёa-z]+(?:ться|ть|ти))\b', re.I)
LAST_INFINITIVE = re.compile(r'[а-яёa-z]+(?:ться|ть|ти)$', re.I)

def split_question(question):
    result=[]
    for sentence in re.split(r'[?;]', question):
        sentence=sentence.strip()
        if not sentence: continue
        pieces=re.split(r'\s+и\s+',sentence,flags=re.I)
        current=pieces[0].strip()
        for right in pieces[1:]:
            right=right.strip()
            verb=INFINITIVE.match(right)
            # Two standalone Latin identifiers can name independent topics.
            identifiers = re.fullmatch(r'[A-Za-z0-9_-]+', current) and re.fullmatch(r'[A-Za-z0-9_-]+', right)
            if not (CLAUSE.match(right) or verb or identifiers):
                current += ' и ' + right
                continue
            # "Как получить и отменить пропуск для гостя": the object and
            # conditions after the second infinitive also qualify the first.
            if verb and LAST_INFINITIVE.search(current):
                shared=right[verb.end():].strip()
                if shared: current += ' ' + shared
            result.append(current)
            current=right
        if current: result.append(current)
    return result
