"""Russian stemming for BM25 and abbreviations defined in the corpus (stage R2).

* Snowball stems replace the former 6-letter prefix truncation, so
  "пропуск / пропуска / пропусков" and "почта / почте" match.
* Definitions in the documents themselves — "Конференц-зал (далее — КЗ)",
  "КЗ — конференц-зал" — are collected at indexing; a question using either
  side is expanded with the other. The manual ALIASES list stays as a fallback.
"""
from functools import lru_cache
import re

STOP = {'и', 'в', 'во', 'на', 'с', 'со', 'по', 'к', 'ко', 'о', 'об', 'от', 'до', 'за', 'из', 'у', 'для', 'при', 'не', 'ли',
        'а', 'но', 'или', 'что', 'как', 'какой', 'какая', 'какие', 'каком', 'куда', 'где', 'когда', 'кто', 'кому', 'чем',
        'это', 'мне', 'я', 'мы', 'вы', 'он', 'она', 'они', 'его', 'ее', 'их', 'же', 'бы', 'то', 'ли', 'можно', 'нужно',
        'надо', 'сколько', 'есть', 'быть', 'ли', 'чтобы', 'если', 'там', 'тут', 'так', 'уже', 'еще', 'все', 'весь'}
WORD = re.compile(r'[a-zа-я0-9]+', re.I)


@lru_cache(maxsize=1)
def _stemmer():
    try:
        import snowballstemmer
        return snowballstemmer.stemmer('russian')
    except Exception:
        return None


def words(text):
    return WORD.findall(text.lower().replace('ё', 'е'))


def stem(word):
    s = _stemmer()
    if s is None or not re.search('[а-я]', word):
        return word[:6] if re.search('[а-я]', word) else word
    return s.stemWord(word)


def stem_text(text):
    """Text for the full-text index: stems separated by spaces."""
    return ' '.join(stem(w) for w in words(text))


def query_stems(question):
    """Stems of the content words of a question."""
    out = []
    for w in words(question):
        if w in STOP or (len(w) < 3 and not w.isdigit()): continue
        s = stem(w)
        if s not in out: out.append(s)
    return out


ABBREVIATION = re.compile(
    r'([А-ЯЁA-Za-zа-яё][\w\-]*(?:\s+[\w\-]+){0,6}?)\s*\(\s*далее\s*[—–-]+\s*([А-ЯЁA-Z]{2,8})\s*\)'
    r'|\b([А-ЯЁA-Z]{2,8})\s+[—–-]\s+([а-яё][\w\-]*(?:\s+[а-яё\w\-]+){0,6})', re.U)


def definitions(text):
    """[(abbreviation, expansion)] defined in a text."""
    out = []
    for m in ABBREVIATION.finditer(text):
        if m.group(2):
            # The defined term is the phrase just before "(далее — X)": take words
            # from the end until their parts (split on hyphens) cover the letters.
            taken, letters = [], 0
            for w in reversed(m.group(1).split()):
                taken.insert(0, w); letters += len(w.split('-'))
                if letters >= len(m.group(2)): break
            while taken and taken[0].lower() in STOP: taken.pop(0)
            out.append((m.group(2), ' '.join(taken)))
        elif m.group(3):
            out.append((m.group(3), m.group(4)))
    return out
