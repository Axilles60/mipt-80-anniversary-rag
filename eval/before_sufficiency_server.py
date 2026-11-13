"""Frozen answer path immediately before the sufficiency experiment."""
from pathlib import Path
import re
import runpy
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import core, retrieval

current_imported = retrieval.imported
def previous_imported(db, question, k=3, **_later_options):  # e.g. gate=; ignored by the frozen path
    # Previous search checked price before limiting the results.
    rows = current_imported(db, question, k=100)
    if re.search(r'цен[аыуеой]|стоимост|тариф', question.lower()):
        rows = [r for r in rows if re.search(r'цен[аыуеой]|стоимост|тариф', r['text'].lower())]
    return rows[:k]
retrieval.imported = previous_imported

def previous_answer(db, question, generator=None):
    if not question.strip(): return 'empty', []
    tables = {r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
    if 'fragments' in tables:
        matches = core.search(db, question)
        if matches and re.search(r'принтер|техподдерж|поддержк|неисправ|заявк', question.lower()) and {r['document_id'] for r in matches} == {'SYN-SUPPORT'}:
            rows = db.execute('SELECT * FROM fragments WHERE document_id=? AND version=? ORDER BY id', ('SYN-SUPPORT', '1')).fetchall()
            if len(rows) == 3: return 'answer', core.extractive(rows)
    if 'import_fragments' in tables:
        rows = retrieval.filter_sibling_cells(previous_imported(db, question, k=3), question)
        rows = [r for r in rows if r['text'].strip()]
        if rows: return 'imported', [(r['text'][:500], r['id']) for r in rows]
    return 'insufficient', []
core.form_answer = previous_answer
sys.argv = ['app']
runpy.run_module('app', run_name='__main__')
