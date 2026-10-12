"""Какие русские фразы интерфейса ещё не переведены (нет в web/i18n/en.json).

Собирает тексты и атрибуты HTML, строки JS и сообщения сервера; печатает те,
что без перевода. Перевод -- в web/i18n/en.json, затем scripts/i18n_build.py.

    python scripts/i18n_missing.py
"""
import glob
import json
import re
import sys
from html.parser import HTMLParser
cyr = re.compile(r'[А-Яа-яЁё]')
frags = {}
def add(s, src):
    for part in re.split(r'\$\{(?:[^{}]|\{[^{}]*\})*\}|<[^>]*>|\{[^{}]*\}|\\n|\n', s):
        part = re.sub(r'\s+', ' ', part).strip()
        part = part.strip('\'"`')
        if cyr.search(part) and len(part) < 400:
            frags.setdefault(part, src)
class P(HTMLParser):
    def __init__(s, src): super().__init__(convert_charrefs=True); s.src=src; s.skip=0
    def handle_starttag(s, tag, attrs):
        if tag in ('script', 'style'): s.skip += 1
        for k, v in attrs:
            if k in ('placeholder', 'title', 'aria-label', 'alt', 'content', 'value') and v and cyr.search(v): add(v, s.src)
    def handle_endtag(s, tag):
        if tag in ('script', 'style'): s.skip -= 1
    def handle_data(s, d):
        if not s.skip and cyr.search(d): add(d, s.src)
SKIP = ('offer.html', 'privacy.html', 'admin.html', 'admin.js')
for f in sorted(glob.glob('web/static/*.html')):
    if f.endswith(SKIP): continue
    p = P(f.split('/')[-1]); p.feed(re.sub(r'<!--.*?-->', '', open(f).read(), flags=re.S))
strlit = re.compile(r"'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"|`(?:[^`\\]|\\.)*`", re.S)
for f in sorted(glob.glob('web/static/*.js')):
    if f.endswith(SKIP) or 'vendor' in f: continue
    s = open(f).read()
    s = re.sub(r'/\*.*?\*/', '', s, flags=re.S)
    s = '\n'.join(re.sub(r'(^|[^:\\])//.*$', r'\1', l) for l in s.splitlines())
    for m in strlit.finditer(s):
        if cyr.search(m.group()): add(m.group()[1:-1], f.split('/')[-1])
pystr = re.compile(r'f?"(?:[^"\\\n]|\\.)*"|f?\'(?:[^\'\\\n]|\\.)*\'')
for f in ['web/app.py', 'web/studio.py', 'web/jobs.py', 'web/billing.py', 'midi2tab/audiochords.py', 'midi2tab/separate.py', 'midi2tab/lyrics.py', 'web/seo.py']:
    for line in open(f):
        code = line.split('#', 1)[0] if '"' not in line.split('#', 1)[-1] else line
        if re.search(r'HTTPException|stage=|stage\s*=|progress\(|bar\.(note|begin)|raise RuntimeError|"why"|why =|title=|Service\(|reason|label|"stage"|\("[А-Я]', line):
            for m in pystr.finditer(line):
                t = m.group().lstrip('f')[1:-1]
                if cyr.search(t): add(t, f.split('/')[-1])
words = json.load(open('web/i18n/en.json', encoding='utf-8'))
skip = ('lyricstags.js', 'seo.py')   # слова распознавания стилей; SEO-страницы -- только по-русски
missing = [(k, v) for k, v in frags.items() if v not in skip and k not in words and len(k) > 2
           and '{' not in k and '}' not in k]
for k, v in missing:
    print(f'{v}\t{k}')
print(f'без перевода: {len(missing)} из {len(frags)}', file=sys.stderr)
