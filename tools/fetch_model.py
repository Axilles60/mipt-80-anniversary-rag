"""Download the optional local embedding model (public files) and verify checksums.

Run once with network access — during `docker build --build-arg WITH_DENSE=1`
or by hand. Only public model files are downloaded; no document or question is
sent anywhere. The service itself never downloads anything.

  python tools/fetch_model.py [TARGET_DIR]   (default data/models/multilingual-e5-small)
"""
import hashlib
from pathlib import Path
import sys
from urllib.request import urlopen

BASE = 'https://huggingface.co/intfloat/multilingual-e5-small/resolve/main/onnx/'
FILES = {
    'model.onnx': (470268510, 'ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665'),
    'tokenizer.json': (17082730, '0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39'),
    'tokenizer_config.json': (443, 'a1d6bc8734a6f635dc158508bef000f8e2e5a759c7d92f984b2c86e5ff53425b'),
    'special_tokens_map.json': (167, 'd05497f1da52c5e09554c0cd874037a083e1dc1b9cfd48034d1c717f1afc07a7'),
    'config.json': (653, 'bbb7c1333fc4b3e27fbc9cd5d2070aabcc1d4dfb99917c3633e772f97545a6b6'),
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
    target = Path(sys.argv[1] if len(sys.argv) > 1 else 'data/models/multilingual-e5-small')
    target.mkdir(parents=True, exist_ok=True)
    for name, (size, sha) in FILES.items():
        path = target / name
        if verified(path, size, sha):
            print('ok (present)', name); continue
        partial = path.with_suffix(path.suffix + '.part')
        with urlopen(BASE + name, timeout=60) as response, open(partial, 'wb') as out:
            for block in iter(lambda: response.read(1 << 20), b''):
                out.write(block)
        if not verified(partial, size, sha):
            partial.unlink(missing_ok=True)
            raise SystemExit('checksum mismatch: ' + name)
        partial.replace(path)
        print('ok (downloaded)', name)


if __name__ == '__main__':
    main()
