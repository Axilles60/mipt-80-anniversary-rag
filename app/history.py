"""Conversation history of the web interface: what each browser asked and was answered.

A browser gets a random conversation ID in a cookie; its turns are kept in the
same SQLite file as the index, so nothing leaves the machine. The history serves
two purposes: the person sees the dialogue, and a follow-up question («а для
бухгалтерии?») is rewritten into a self-contained one before retrieval
(rag.condense). NAVIGATOR_HISTORY=0 turns storage off (every question stands alone).
"""
import os
import re
import secrets

SCHEMA = '''CREATE TABLE IF NOT EXISTS chat_turns (
    conversation TEXT NOT NULL, n INTEGER NOT NULL, question TEXT NOT NULL, standalone TEXT NOT NULL,
    answer_text TEXT NOT NULL, answer_html TEXT NOT NULL, asked_on TEXT,
    created TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(conversation, n))'''
COOKIE = 'nav_chat'
KEEP = 50            # turns shown and kept per conversation


def enabled():
    return os.environ.get('NAVIGATOR_HISTORY', '1').strip().lower() not in ('0', 'off', 'false', 'no')


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


def turns(db, conversation, limit=KEEP):
    db.execute(SCHEMA)
    rows = db.execute('SELECT * FROM chat_turns WHERE conversation=? ORDER BY n DESC LIMIT ?', (conversation, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def add(db, conversation, question, standalone, answer_text, answer_html, asked_on=None):
    db.execute(SCHEMA)
    n = (db.execute('SELECT MAX(n) FROM chat_turns WHERE conversation=?', (conversation,)).fetchone()[0] or 0) + 1
    db.execute('INSERT INTO chat_turns(conversation, n, question, standalone, answer_text, answer_html, asked_on) '
               'VALUES (?,?,?,?,?,?,?)', (conversation, n, question, standalone, answer_text, answer_html, asked_on))
    db.execute('DELETE FROM chat_turns WHERE conversation=? AND n<=?', (conversation, n - KEEP))
    return n


def clear(db, conversation):
    db.execute(SCHEMA)
    db.execute('DELETE FROM chat_turns WHERE conversation=?', (conversation,))


def plain(answer_html):
    """The answer as text for the follow-up rewriting: statements only, no citations or service lines."""
    text = re.sub(r'<a [^>]*>.*?</a>', ' ', answer_html, flags=re.S)
    text = re.sub(r'<[^>]+>', '\n', text)
    import html
    lines = [html.unescape(line).strip() for line in text.split('\n')]
    skip = ('Источник:', 'Применимость', 'Самопроверка', 'Ограничение:', 'Ответ')
    return ' '.join(line for line in lines if line and not line.startswith(skip))[:600]
