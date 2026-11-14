"""Static checks of docker-compose.yml and Dockerfile.llm-gpu (not run in this environment).

The GPU generator image is pinned by digest, its model is fetched with a pinned
checksum, the file it serves is the file fetched, it is not published to the
host, and the navigator reaches it by a compose service name.
"""
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import fetch_model  # noqa: E402

try:
    import yaml
except ImportError:          # the image has no PyYAML; the check runs in development
    yaml = None


@unittest.skipUnless(yaml, 'PyYAML is not installed')
class ComposeStaticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = yaml.safe_load((ROOT / 'docker-compose.yml').read_text(encoding='utf-8'))
        cls.llm_dockerfile = (ROOT / 'Dockerfile.llm-gpu').read_text(encoding='utf-8')

    def test_gpu_image_pinned_by_digest(self):
        base = re.findall(r'^FROM (\S+)', self.llm_dockerfile, re.M)[-1]
        self.assertRegex(base, r'@sha256:[0-9a-f]{64}$')

    def test_served_model_is_the_fetched_one(self):
        llm = self.compose['services']['llm']
        model = llm['build']['args']['LLM']
        served = Path(llm['command'][llm['command'].index('-m') + 1]).name
        self.assertIn(served, fetch_model.MODELS[model][1])

    def test_generator_is_internal_and_ui_local(self):
        services = self.compose['services']
        self.assertNotIn('ports', services['llm'])
        for name in ('navigator', 'navigator-gpu'):
            for port in services[name]['ports']:
                self.assertTrue(port.startswith('127.0.0.1:'), port)
        self.assertEqual(services['navigator-gpu']['environment']['NAVIGATOR_LLM_URL'], 'http://llm:8080')


if __name__ == '__main__':
    unittest.main()
