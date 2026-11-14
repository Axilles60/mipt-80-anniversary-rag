"""Import fictional files with the standard CLI and query POST /ask on loopback."""
from contextlib import closing
from html.parser import HTMLParser
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


class Items(HTMLParser):
    """Answer paragraphs and list items with their citation IDs and headings."""
    def __init__(self):
        super().__init__(); self.items = []; self.item = None; self.headings = {}; self.current = None; self.in_link = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('h2', 'h3', 'h4', 'h5'): self.current = tag; self.headings[tag] = ''
        if tag in ('li', 'p'): self.item = {'text': '', 'plain': '', 'links': [], 'hrefs': [], 'headings': dict(self.headings)}
        if tag == 'a': self.in_link += 1
        if tag == 'a' and self.item is not None:
            href = dict(attrs).get('href', '')
            if urlsplit(href).path == '/fragment':
                self.item['hrefs'].append(href); self.item['links'].append(parse_qs(urlsplit(href).query)['id'][0])
    def handle_data(self, data):
        if self.current: self.headings[self.current] += data
        if self.item is not None:
            self.item['text'] += data
            if not self.in_link: self.item['plain'] += data   # without citation labels («стр. 2»)
    def handle_endtag(self, tag):
        if tag == 'a' and self.in_link: self.in_link -= 1
        if tag == self.current:
            level = int(tag[1])
            for lower in ('h3', 'h4', 'h5'):
                if int(lower[1]) > level: self.headings.pop(lower, None)
            self.current = None
        if tag in ('li', 'p') and self.item is not None: self.items.append(self.item); self.item = None


class Session:
    """Temporary database, standard import and a loopback web server."""
    def __init__(self, build):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        manifest = self.root / 'manifest.json'; manifest.write_text('[]')
        self.env = dict(os.environ, NAVIGATOR_DB=str(self.root / 'db'), HOST='127.0.0.1'); self.env.pop('NAVIGATOR_MODEL', None)
        paths = [str(p) for p in build(self.root)]
        subprocess.run([sys.executable, '-m', 'app', 'import', *paths, '--manifest', str(manifest)],
                       env=self.env, cwd=ROOT, check=True, capture_output=True)
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); self.env['PORT'] = str(s.getsockname()[1])
        self.base = 'http://127.0.0.1:' + self.env['PORT']
        self.server = subprocess.Popen([sys.executable, '-m', 'app'], env=self.env, cwd=ROOT,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(80):
            try: urlopen(self.base + '/', timeout=1).close(); break
            except OSError: time.sleep(.1)
        else:
            self.close(); raise RuntimeError('server unavailable')

    def fragments(self):
        from app.core import connect
        with closing(connect(self.env['NAVIGATOR_DB'])) as db:
            return [dict(r) for r in db.execute('SELECT * FROM import_fragments ORDER BY rowid')]

    def ask(self, question, date='', category=''):
        page = urlopen(self.base + '/ask', data=urlencode({'question': question, 'date': date, 'category': category}).encode(),
                       timeout=10).read().decode()
        parsed = Items(); parsed.feed(page)
        return page, parsed.items

    def open(self, href):
        return urlopen(urljoin(self.base, href), timeout=10).read().decode()

    def close(self):
        self.server.terminate(); self.server.wait(timeout=5); self.temp.cleanup()

    def __enter__(self): return self
    def __exit__(self, *exc): self.close()
