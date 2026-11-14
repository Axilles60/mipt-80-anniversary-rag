"""Benchmark-only frozen imported search from before the current experiment."""
from pathlib import Path
import runpy
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import retrieval

def baseline(db, question, k=3, **_later_options):  # e.g. gate=; the frozen search ignores them
    tokens=retrieval.terms(question)
    if not tokens: return []
    query=' OR '.join('"'+x+'"*' for x in tokens)
    return db.execute('SELECT f.* FROM import_fts x JOIN import_fragments f ON f.id=x.id WHERE import_fts MATCH ? ORDER BY bm25(import_fts),f.id LIMIT ?', (query,k)).fetchall()

retrieval.imported=baseline
sys.argv=['app']
runpy.run_module('app',run_name='__main__')
