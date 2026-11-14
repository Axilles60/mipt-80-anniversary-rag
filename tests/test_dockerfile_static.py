"""Static checks of the Dockerfile (the image itself is not built in this environment).

Every file it copies exists and is allowed by .dockerignore; every model it
fetches is pinned (URL, size, SHA-256) in tools/fetch_model.py; the model paths
it sets match the directories it fetches into.
"""
import re
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import fetch_model  # noqa: E402

DOCKERFILE = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
IGNORE = (ROOT / '.dockerignore').read_text(encoding='utf-8').split()


class DockerfileStaticTest(unittest.TestCase):
    def test_copied_files_exist_and_are_not_ignored(self):
        for line in re.findall(r'^COPY (?!--from)(.+)$', DOCKERFILE, re.M):
            for source in line.split()[:-1]:
                with self.subTest(source=source):
                    self.assertTrue((ROOT / source).exists())
                    allowed = '!' + source.rstrip('/') in [p.rstrip('/') for p in IGNORE]
                    self.assertTrue(allowed, f'{source} is excluded by .dockerignore')

    def test_fetched_models_are_pinned(self):
        fetched = re.findall(r'fetch_model\.py (\w+) (\S+?)[;\s]', DOCKERFILE)
        self.assertEqual({name for name, _ in fetched}, {'e5', 'reranker', 'qwen'})
        for name, target in fetched:
            with self.subTest(model=name):
                _, files = fetch_model.MODELS[name]
                for url, size, sha in files.values():
                    self.assertTrue(url.startswith('https://huggingface.co/'))
                    self.assertGreater(size, 0)
                    self.assertRegex(sha, r'^[0-9a-f]{64}$')

    def test_model_paths_match_fetch_targets(self):
        targets = dict(re.findall(r'fetch_model\.py (\w+) (\S+?)[;\s]', DOCKERFILE))
        env = dict(re.findall(r'(NAVIGATOR_\w+)=(\S+)', DOCKERFILE))
        self.assertEqual(env['NAVIGATOR_EMBED_MODEL'], targets['e5'])
        self.assertEqual(env['NAVIGATOR_RERANK_MODEL'], targets['reranker'])
        self.assertTrue(env['NAVIGATOR_LLM'].startswith(targets['qwen'] + '/'))
        self.assertIn(Path(env['NAVIGATOR_LLM']).name, fetch_model.MODELS['qwen'][1])


if __name__ == '__main__':
    unittest.main()
