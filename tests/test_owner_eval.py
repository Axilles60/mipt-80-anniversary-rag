"""eval/owner_eval.py on a fictional corpus: metrics, reports, the database untouched."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'eval'))
from synthetic_docs import SUPPORT_PAGES, TRIP_PAGES, SUPPORT_BUDGET, write_pdf, write_xlsx  # noqa: E402

QUESTIONS = {'questions': [
    {'id': 'S1', 'ac': 'AC-DEMO-01', 'topic': 'it', 'variant': 'direct', 'expect': 'answer',
     'question': 'Не работает принтер, куда обратиться и что написать?', 'expected_sources': ['SRC-02'],
     'facts': ['portal\\.example\\.invalid/support', '16 рабочих час'], 'forbidden': ['подрядчиком']},
    {'id': 'S2', 'ac': 'AC-DEMO-02', 'topic': 'trip', 'variant': 'direct', 'expect': 'answer',
     'question': 'Обычная служебная поездка: когда подать документы и что делать после возвращения?',
     'expected_sources': ['SRC-05'], 'facts': ['ФП-52'], 'forbidden': []},
    {'id': 'N1', 'ac': 'AC-SYS-08', 'topic': 'negative', 'variant': 'negative', 'expect': 'insufficient',
     'question': 'Как оформить ежегодный оплачиваемый отпуск?', 'expected_sources': [], 'facts': [], 'forbidden': []}]}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class OwnerEvalTest(unittest.TestCase):
    def test_report_on_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = [write_pdf(root / 'support.pdf', SUPPORT_PAGES), write_pdf(root / 'trip.pdf', TRIP_PAGES),
                     write_xlsx(root / 'budget.xlsx', SUPPORT_BUDGET)]
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps([{'id': 'SRC-02', 'name': 'support.pdf', 'sha256': '-'},
                                            {'id': 'SRC-05', 'name': 'trip.pdf', 'sha256': '-'},
                                            {'id': 'SRC-13', 'name': 'budget.xlsx', 'sha256': '-'}]), encoding='utf-8')
            db = root / 'owner.sqlite3'
            env = dict(os.environ, NAVIGATOR_DB=str(db)); env.pop('NAVIGATOR_RETRIEVAL', None)
            subprocess.run([sys.executable, '-m', 'app', 'import', *map(str, files), '--manifest', str(manifest)],
                           cwd=ROOT, env=env, check=True, capture_output=True)
            questions = root / 'questions.json'
            questions.write_text(json.dumps(QUESTIONS, ensure_ascii=False), encoding='utf-8')
            before = digest(db)
            report = root / 'runs' / 'owner-eval.json'
            command = [sys.executable, 'eval/owner_eval.py', '--db', str(db), '--report', str(report),
                       '--questions', str(questions)]
            run = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
            self.assertEqual(run.returncode, 0, run.stderr[-2000:])
            self.assertEqual(digest(db), before)          # the owner database is not modified
            data = json.loads(report.read_text(encoding='utf-8'))
            entry = data['modes']['bm25']
            verdicts = {r['id']: r['verdict'] for r in entry['answers']}
            self.assertEqual(verdicts, {'S1': 'correct_source', 'S2': 'correct_source', 'N1': 'correct_refusal'})
            s1 = next(r for r in entry['answers'] if r['id'] == 'S1')
            self.assertEqual(s1['facts_found'], '2/2'); self.assertEqual(s1['forbidden_found'], [])
            self.assertEqual(entry['summary']['false_answers'], 0)
            self.assertEqual(entry['summary']['citation_precision'], 1.0)
            # BM25 ranks another document first for the trip question; the
            # regulation is chosen later by document selection (answer is correct).
            self.assertEqual(entry['ranking']['recall@3'], 1.0)
            self.assertEqual(entry['ranking']['recall@1'], 0.5)
            self.assertIn('| S1 | AC-DEMO-01 |', report.with_suffix('.md').read_text(encoding='utf-8'))
            again = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertNotEqual(again.returncode, 0)       # existing reports are never overwritten


if __name__ == '__main__':
    unittest.main()
