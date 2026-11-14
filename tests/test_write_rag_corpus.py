"""The README use cases start from the fictional corpus written by eval/write_rag_corpus.py."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'eval'))
import write_rag_corpus  # noqa: E402


class WriteCorpusTest(unittest.TestCase):
    def test_writes_every_document_once(self):
        corpus = json.loads((ROOT / 'fixtures' / 'rag' / 'corpus.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'originals'
            self.assertEqual(write_rag_corpus.main([str(folder)]), 0)
            self.assertEqual(sorted(p.name for p in folder.iterdir()), sorted(d['file'] for d in corpus['documents']))
            # A second run must not mix with what is already there.
            with self.assertRaises(SystemExit):
                write_rag_corpus.main([str(folder)])


if __name__ == '__main__':
    unittest.main()
