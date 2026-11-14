"""Write the fictional regulation corpus fixtures/rag/corpus.json as document files.

  python eval/write_rag_corpus.py data/demo/originals

The 16 PDF, DOCX and XLSX files are the ones the RAG evaluation imports
(eval/measure_rag.py). They are fictional: use them to reproduce the README use
cases without real documents. The folder must be empty or not exist.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from measure_rag import build  # noqa: E402


def main(argv):
    if len(argv) != 1:
        raise SystemExit(__doc__)
    folder = Path(argv[0])
    if folder.exists() and any(folder.iterdir()):
        raise SystemExit(f'not empty: {folder}')
    folder.mkdir(parents=True, exist_ok=True)
    paths, _ = build(folder)
    print(f'{len(paths)} fictional documents written to {folder}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
