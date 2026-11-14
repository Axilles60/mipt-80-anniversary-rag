"""Infrastructure findings of the external audit of commit d23f0fc.

- an act that comes into force later is not applied when the question has no date;
- two files with one name in different folders are two documents, and a file
  imported before under its plain name keeps its document;
- a cached chunk vector goes with its chunk when the chunks are rebuilt;
- files of an unsupported format are reported, not silently skipped.
"""
from contextlib import closing
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'eval')]
from app import autoimport, chunking, requisites  # noqa: E402
from app.core import connect  # noqa: E402
from app.importer import batch, file_names  # noqa: E402
from synthetic_docs import write_docx  # noqa: E402


class FutureActTest(unittest.TestCase):
    def test_future_act_is_not_applied_without_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = write_docx(Path(tmp) / 'future.docx', [
                'ПРИКАЗ от 15.12.2034 № 999-1', 'О порядке пропуска (учебный вымышленный документ)',
                '1. Постоянный пропуск выдаётся в течение 1 рабочего дня.', '2. Настоящий приказ вводится в действие с 01.02.2035.'])
            db_path = Path(tmp) / 'db'
            batch(str(db_path), [str(doc)])
            with closing(connect(str(db_path))) as db:
                key = tuple(db.execute('SELECT source_id, version FROM imports').fetchone())
                self.assertEqual(requisites.status(db, [key])[key][0], 'not_yet')
                self.assertEqual(requisites.status(db, [key], '2035-03-01')[key][0], 'ok')


class SourceIdTest(unittest.TestCase):
    def test_same_name_in_two_folders_is_two_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = write_docx((root / 'hr').mkdir() or root / 'hr' / 'rules.docx', ['ПРАВИЛА отдела кадров', '1. Пункт.'])
            b = write_docx((root / 'it').mkdir() or root / 'it' / 'rules.docx', ['ПРАВИЛА ИТ-службы', '1. Другой пункт.'])
            top = write_docx(root / 'top.docx', ['ПОЛОЖЕНИЕ', '1. Пункт.'])
            names = file_names([str(a), str(b), str(top)], root=str(root))
            self.assertEqual(sorted(names.values()), ['hr/rules.docx', 'it/rules.docx', 'top.docx'])
            # Without a folder (CLI import): plain names, the folder only where names clash.
            self.assertEqual(file_names([str(top)]), {str(top): 'top.docx'})
            self.assertEqual(sorted(file_names([str(a), str(b)]).values()), ['hr/rules.docx', 'it/rules.docx'])
            db_path = root / 'db'
            batch(str(db_path), [str(a), str(b)], root=str(root))
            with closing(connect(str(db_path))) as db:
                self.assertEqual(db.execute('SELECT COUNT(DISTINCT source_id) FROM imports').fetchone()[0], 2)

    def test_document_imported_under_plain_name_keeps_its_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'hr').mkdir()
            doc = write_docx(root / 'hr' / 'rules.docx', ['ПРАВИЛА отдела кадров', '1. Пункт.'])
            db_path = root / 'db'
            batch(str(db_path), [str(doc)])                      # an index built before: plain name
            write_docx(doc, ['ПРАВИЛА отдела кадров', '1. Пункт в новой редакции.'])
            batch(str(db_path), [str(doc)], root=str(root))      # now under its path: a new version
            with closing(connect(str(db_path))) as db:
                rows = db.execute('SELECT source_id, version FROM imports').fetchall()
                self.assertEqual(len({r['source_id'] for r in rows}), 1)
                self.assertEqual(len(rows), 2)


class ChunkVectorTest(unittest.TestCase):
    def test_vectors_go_with_rebuilt_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = write_docx(Path(tmp) / 'a.docx', ['ПОЛОЖЕНИЕ', '1. Пункт один.'])
            db_path = Path(tmp) / 'db'
            batch(str(db_path), [str(doc)])
            with closing(connect(str(db_path))) as db:
                chunking.ensure(db)
                from app import dense
                db.execute(dense.SCHEMA.format(table='chunk_vectors'))
                ids = [r[0] for r in db.execute('SELECT id FROM chunks WHERE level=2')]
                db.executemany('INSERT INTO chunk_vectors VALUES (?, 0, ?, ?)', [(i, 'm', b'x') for i in ids]); db.commit()
                key = tuple(db.execute('SELECT source_id, version FROM imports').fetchone())
                chunking.index_version(db, *key)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM chunk_vectors').fetchone()[0], 0)


class SkippedFilesTest(unittest.TestCase):
    def test_unsupported_files_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'originals'; folder.mkdir()
            write_docx(folder / 'a.docx', ['ПОЛОЖЕНИЕ', '1. Пункт.'])
            (folder / 'notes.txt').write_text('x'); (folder / 'old.doc').write_bytes(b'x')
            lines = []
            autoimport.run(str(Path(tmp) / 'db'), str(folder), log=lines.append)
            self.assertTrue(any('Not imported' in line and 'notes.txt' in line and 'old.doc' in line for line in lines), lines)


if __name__ == '__main__':
    unittest.main()
