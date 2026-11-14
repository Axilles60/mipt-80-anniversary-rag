"""Conversation history of the web interface: what each browser asked and was answered.

A browser gets a random conversation ID in a cookie; its turns are kept in the
same SQLite file as the index, so nothing leaves the machine. The history serves
two purposes: the person sees the dialogue, and a question that refers to the
dialogue («а для бухгалтерии?», «а где их кабинет?») is understood through it.

How a new question relates to the session (`relation`):
- «new» — it names its own subject and refers to nothing: it is answered as asked,
  the model is not asked, the date of earlier questions is not carried over;
- «follow» — it refers to the dialogue (pronouns and demonstratives, a leading «а / и /
  тогда / ещё», no subject of its own, or only a few words): the model rewrites it
  into a self-contained question from the earlier turns *as they were understood*
  (so a chain of follow-ups keeps its subject), and a date given earlier carries over
  unless the new question has its own.
The session is the turns of the last NAVIGATOR_HISTORY_WINDOW minutes (120): older
turns stay visible but no longer shape how new questions are understood.
NAVIGATOR_HISTORY=0 turns storage off (every question stands alone).
"""
from datetime import datetime, timedelta, timezone
import os
import re
import secrets

SCHEMA = '''CREATE TABLE IF NOT EXISTS chat_turns (
    conversation TEXT NOT NULL, n INTEGER NOT NULL, question TEXT NOT NULL, standalone TEXT NOT NULL,
    answer_text TEXT NOT NULL, answer_html TEXT NOT NULL, asked_on TEXT,
    created TEXT DEFAULT CURRENT_TIMESTAMP, relation TEXT DEFAULT 'new', ref INTEGER, PRIMARY KEY(conversation, n))'''
COOKIE = 'nav_chat'
KEEP = 50            # turns shown and kept per conversation
CONTEXT_TURNS = 3    # turns given to the rewriting

# Words that point back into the dialogue.
REFERS = re.compile(r'(?<![\w-])(?:он|она|оно|они|его|её|ее|их|им|ими|ему|ей|нём|нем|ней|них|него|неё|нее|'
                    r'там|туда|оттуда|тут|здесь|этот|эта|это|эти|этого|этой|этим|этих|этому|эту|'
                    r'тот же|та же|то же|те же|такой же|такая же|такие же|такого же|тоже|также|'
                    r'раньше|ранее|прежде|в этом случае|при этом)(?![\w-])', re.I)
# «А сейчас?» after a question on a past date asks about today: the date does not carry over.
NOW = re.compile(r'(?<![\w-])(?:сейчас|теперь|сегодня|ныне|нынешн\w*|в настоящее время|действующ\w*|текущ\w*)(?![\w-])', re.I)
LEADS = re.compile(r'^\s*(?:а|и|ну|тогда|ещё|еще|а если|а ещё|а еще|а что|а как|а где|а когда|а кто|а сколько)\b', re.I)


def enabled():
    return os.environ.get('NAVIGATOR_HISTORY', '1').strip().lower() not in ('0', 'off', 'false', 'no')


def window():
    try:
        return max(1, int(os.environ.get('NAVIGATOR_HISTORY_WINDOW', '120')))
    except ValueError:
        return 120


def new_id():
    return secrets.token_urlsafe(18)


def valid(conversation):
    return bool(conversation) and re.fullmatch(r'[A-Za-z0-9_-]{20,64}', conversation) is not None


def from_cookie(header):
    """The conversation ID from a Cookie header, or None."""
    for part in (header or '').split(';'):
        name, _, value = part.strip().partition('=')
        if name == COOKIE and valid(value):
            return value
    return None


def ensure(db):
    db.execute(SCHEMA)
    columns = {r[1] for r in db.execute('PRAGMA table_info(chat_turns)')}
    if 'relation' not in columns:           # a base made before relations were kept
        db.execute("ALTER TABLE chat_turns ADD COLUMN relation TEXT DEFAULT 'new'")
    if 'ref' not in columns:                # the turn a follow-up refers to
        db.execute('ALTER TABLE chat_turns ADD COLUMN ref INTEGER')


def turns(db, conversation, limit=KEEP):
    ensure(db)
    rows = db.execute('SELECT * FROM chat_turns WHERE conversation=? ORDER BY n DESC LIMIT ?', (conversation, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def session(db, conversation, now=None):
    """The turns that shape how the next question is understood: the last few, within the window."""
    recent = turns(db, conversation, CONTEXT_TURNS)
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(minutes=window())).strftime('%Y-%m-%d %H:%M:%S')
    return [t for t in recent if (t.get('created') or '') >= since]


def add(db, conversation, question, standalone, answer_text, answer_html, asked_on=None, relation='new', ref=None):
    ensure(db)
    n = (db.execute('SELECT MAX(n) FROM chat_turns WHERE conversation=?', (conversation,)).fetchone()[0] or 0) + 1
    db.execute('INSERT INTO chat_turns(conversation, n, question, standalone, answer_text, answer_html, asked_on, relation, ref) '
               'VALUES (?,?,?,?,?,?,?,?,?)', (conversation, n, question, standalone, answer_text, answer_html, asked_on, relation, ref))
    db.execute('DELETE FROM chat_turns WHERE conversation=? AND n<=?', (conversation, n - KEEP))
    return n


def clear(db, conversation):
    ensure(db)
    db.execute('DELETE FROM chat_turns WHERE conversation=?', (conversation,))


def relation(question, earlier):
    """«follow» when the question refers to the earlier turns, «new» when it stands alone.

    Decided by code, before any model: a question with its own subject and no word
    pointing back is a new topic even mid-dialogue («Где взять бланк пропуска?» after
    a question about VPN), so it is never mixed with the previous one."""
    if not earlier:
        return 'new'
    text = question.strip()
    if REFERS.search(text) or LEADS.match(text):
        return 'follow'
    words = re.findall(r'[А-Яа-яЁёA-Za-z]+', text)
    from .rag import question_terms
    try:
        own = question_terms(text)
    except Exception:          # no morphology: count longer words instead
        own = [w for w in words if len(w) > 4]
    return 'new' if own else 'follow'


def plain(answer_html):
    """The answer as text for the rewriting: statements only, no citations, quotes or service lines."""
    text = re.sub(r'<div class="quotes">.*', ' ', answer_html, flags=re.S)        # quotes and sources
    text = re.sub(r'<a [^>]*>.*?</a>', ' ', text, flags=re.S)
    text = re.sub(r'<[^>]+>', '\n', text)
    import html
    lines = [html.unescape(line).strip() for line in text.split('\n')]
    skip = ('Источник:', 'Применимость', 'Самопроверка', 'Ограничение:', 'Ответ', 'Фрагменты документов',
            'Источники и ограничения', 'Недостаточно данных для части')
    return ' '.join(line for line in lines if line and not line.startswith(skip))[:600]
