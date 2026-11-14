"""Download optional local models (public files) and verify checksums.

Run once with network access — during `docker build` or by hand. Only public
model files are downloaded, pinned to a revision; no document or question is
sent anywhere. The service itself never downloads anything.

  python tools/fetch_model.py [e5|reranker] [TARGET_DIR]
  python tools/fetch_model.py [TARGET_DIR]          (e5, as before)

e5        intfloat/multilingual-e5-small, ONNX     -> data/models/multilingual-e5-small
reranker  BAAI/bge-reranker-v2-m3, ONNX int8        -> data/models/bge-reranker-v2-m3
          (onnx-community conversion, Apache-2.0)
"""
import hashlib
from pathlib import Path
import sys
from urllib.request import urlopen

E5_FILES = {
    'model.onnx': (470268510, 'ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665'),
    'tokenizer.json': (17082730, '0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39'),
    'tokenizer_config.json': (443, 'a1d6bc8734a6f635dc158508bef000f8e2e5a759c7d92f984b2c86e5ff53425b'),
    'special_tokens_map.json': (167, 'd05497f1da52c5e09554c0cd874037a083e1dc1b9cfd48034d1c717f1afc07a7'),
    'config.json': (653, 'bbb7c1333fc4b3e27fbc9cd5d2070aabcc1d4dfb99917c3633e772f97545a6b6'),
}
RERANKER = 'https://huggingface.co/onnx-community/bge-reranker-v2-m3-ONNX/resolve/6f5ff65298512715a1e669753bc754d2bc8f367b/'
MODELS = {
    # name: (default target, {local name: (url, size, sha256)})
    'e5': ('data/models/multilingual-e5-small',
           {n: ('https://huggingface.co/intfloat/multilingual-e5-small/resolve/main/onnx/' + n, size, sha)
            for n, (size, sha) in E5_FILES.items()}),
    'reranker': ('data/models/bge-reranker-v2-m3', {
        'model_int8.onnx': (RERANKER + 'onnx/model_int8.onnx', 570727094,
                            '912fc1215c2dbff6499700534bd8d31253af01573861abbfc43afd1fab6cce5d'),
        'tokenizer.json': (RERANKER + 'tokenizer.json', 17082900,
                           '8bf8afbfd11306bd872018c53bfdf2e160a56f8edbcf49933324404791c148d3'),
        'tokenizer_config.json': (RERANKER + 'tokenizer_config.json', 1203,
                                  'b87c8703482b0300d3da30e201519aa641f6a450f5eb5bf1e624afbf70c74d80'),
        'config.json': (RERANKER + 'config.json', 848,
                        '122e922dcfed6503c8721e6fe1daf090340c3d95ca7f3aa3a72730b321a51cfd'),
    }),
}


def verified(path, size, sha):
    if not path.is_file() or path.stat().st_size != size:
        return False
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest() == sha


def main():
    args = sys.argv[1:]
    which = args.pop(0) if args and args[0] in MODELS else 'e5'
    default, files = MODELS[which]
    target = Path(args[0] if args else default)
    target.mkdir(parents=True, exist_ok=True)
    for name, (url, size, sha) in files.items():
        path = target / name
        if verified(path, size, sha):
            print('ok (present)', name); continue
        partial = path.with_suffix(path.suffix + '.part')
        with urlopen(url, timeout=60) as response, open(partial, 'wb') as out:
            for block in iter(lambda: response.read(1 << 20), b''):
                out.write(block)
        if not verified(partial, size, sha):
            partial.unlink(missing_ok=True)
            raise SystemExit('checksum mismatch: ' + name)
        partial.replace(path)
        print('ok (downloaded)', name)


if __name__ == '__main__':
    main()
