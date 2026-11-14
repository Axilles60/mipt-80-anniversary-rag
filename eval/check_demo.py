"""Smoke test of web form using fictional examples only."""
import argparse
from html.parser import HTMLParser
import json
import time
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import urlopen

class Links(HTMLParser):
    def __init__(self): super().__init__(); self.links=[]
    def handle_starttag(self, tag, attrs):
        if tag=='a':
            a=dict(attrs)
            if a.get('href','').startswith('/fragment?'): self.links.append(a['href'])

def ask(base,question,when=''):
    data=urlencode({'question':question,'date':when,'category':''}).encode()
    return urlopen(urljoin(base,'/ask'),data=data,timeout=15).read().decode()

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--url',default='http://127.0.0.1:8000'); parser.add_argument('--report',required=True); args=parser.parse_args()
    for attempt in range(30):
        try:
            with urlopen(args.url, timeout=1) as response:
                if response.status == 200: break
        except OSError:
            if attempt == 29: raise
            time.sleep(0.2)
    results=[]
    for name,question,date,expected in [
        ('support','Не работает принтер, куда обратиться и что написать?','','SYN-SUPPORT-v1-p3'),
        ('travel','Обычная многодневная командировка: когда подать документы и что делать после возвращения?','2023-01-01','T1'),
    ]:
        page=ask(args.url,question,date); links=Links(); links.feed(page)
        href=next((x for x in links.links if expected in x),None)
        if not href: raise RuntimeError(name+': expected reference not found')
        fragment=urlopen(urljoin(args.url,href),timeout=15).read().decode()
        if expected=='SYN-SUPPORT-v1-p3' and ('16 рабочих часов' not in fragment or 'не является сроком устранения' not in fragment): raise RuntimeError('support deadline mismatch')
        if expected=='T1' and ('Вымышленная поездка' not in fragment or 'п. 1' not in fragment): raise RuntimeError('travel source mismatch')
        results.append({'scenario':name,'question_sent':True,'fragment_opened':True,'id':expected})
    page=ask(args.url,'Как оформить отпуск?')
    if 'Недостаточно данных' not in page: raise RuntimeError('unknown question did not produce insufficient state')
    results.append({'scenario':'unknown','question_sent':True,'insufficient':True})
    target=Path(args.report); target.parent.mkdir(parents=True,exist_ok=True); target.write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    print('Synthetic web checks:',len(results),'passed; source pages opened:',2)

if __name__=='__main__': main()
