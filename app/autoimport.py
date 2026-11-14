"""Import every supported document of a folder before the service starts.

Used by `python -m app` (serve) when NAVIGATOR_IMPORT_DIR is set, as in the
container: the operator only places the original documents in the mounted
folder and starts the container. Everything — extraction, OCR, fragments,
indexing and (in hybrid mode) local embeddings — runs in this process; nothing
leaves the machine.

* Files are found recursively: .pdf, .docx, .xlsx and .zip archives.
* A file whose checksum is already imported is skipped ("unchanged"), so a
  restart with a persistent index is fast; a changed file becomes a new version.
* A damaged or unsupported file gets status "error" with its reason and does not
  stop the others (DOC-07); the service starts anyway.
* NAVIGATOR_MANIFEST (optional) maps file names/checksums to registry IDs.
"""
import os
from pathlib import Path
import time

SUPPORTED = {'.pdf', '.docx', '.xlsx', '.zip'}


def documents(directory):
    root = Path(directory)
    if not root.is_dir():
        return []
    return sorted(str(p) for p in root.rglob('*')
                  if p.is_file() and p.suffix.lower() in SUPPORTED
                  and not any(part.startswith('.') or part == '__MACOSX' for part in p.relative_to(root).parts))


def skipped(directory):
    """Files of the folder that are not imported (unsupported format), by path under it."""
    root = Path(directory)
    if not root.is_dir():
        return []
    return sorted(p.relative_to(root).as_posix() for p in root.rglob('*')
                  if p.is_file() and p.suffix.lower() not in SUPPORTED
                  and not any(part.startswith('.') or part == '__MACOSX' for part in p.relative_to(root).parts))


def run(db_path, directory, manifest=None, log=print):
    from .importer import batch
    paths = documents(directory)
    manifest = manifest if manifest and Path(manifest).is_file() else None
    started = time.perf_counter()
    log(f'Import: {len(paths)} file(s) in {directory}' + (f', manifest {manifest}' if manifest else ''))
    other = skipped(directory)
    if other:
        log(f'Not imported (format is not PDF, DOCX, XLSX or ZIP): {len(other)}: ' + ', '.join(other[:20])
            + (' …' if len(other) > 20 else ''))
    # Documents are known by their path under the folder (rules.docx in two folders: two documents).
    counts = batch(db_path, paths, manifest, root=directory)   # also creates an empty schema when there are no files
    log('Import finished in %.1f s: %s' % (time.perf_counter() - started,
                                           ', '.join(f'{k} {v}' for k, v in sorted(counts.items())) or 'nothing to import'))
    from . import dense
    if dense.mode() != 'bm25':
        enc = dense.encoder()
        if enc is None:
            log('Dense model unavailable, BM25 is used: ' + dense.status().get('fallback', ''))
        else:
            from contextlib import closing
            from .core import connect
            with closing(connect(db_path)) as db:
                log(f'Embedded fragments: {dense.ensure_vectors(db, enc)} ({enc.name})')
                from . import rag
                if rag.enabled():
                    # The RAG answer searches chunks: build and embed them now, not at the first question.
                    from . import chunking
                    chunking.ensure(db)
                    log(f'Embedded chunks: {dense.ensure_vectors(db, enc, target="chunks")} ({enc.name})')
    return counts


def from_environment(db_path, log=print):
    directory = os.environ.get('NAVIGATOR_IMPORT_DIR')
    if not directory:
        return None
    return run(db_path, directory, os.environ.get('NAVIGATOR_MANIFEST'), log)
