#!/usr/bin/env python3
"""
B2B BlogIntel — Recruitment-tech competitor crawler + injector
Sites: TestGorilla, Greenhouse, Lever, Workable, Indeed Hire, LinkedIn Talent,
       HackerRank, HackerEarth, iMocha
- RSS feeds + full sitemap crawl per site
- NEVER reduces post count vs existing data (uses static fallback if live too thin)
- Runs daily via GitHub Actions
"""
import urllib.request, urllib.error, gzip, re, os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

TODAY     = datetime.now(timezone.utc).strftime('%Y-%m-%d')
HTML_FILE = 'index.html'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

# ── B2B category guesser ──────────────────────────────────────────
def guess_cat(title, url=''):
    t = (title + ' ' + url).lower()
    if re.search(r'sourc|talent pool|candidate pipeline|outbound|boolean|find candidates|hiring event', t): return 'Talent Sourcing'
    if re.search(r'assess|skills? test|coding (test|challenge)|aptitude|psychometric|proctor|screening test|pre-employment', t): return 'Candidate Assessment'
    if re.search(r'award|\bg2\b|named #1|recogni|certif|announce|milestone|partnership', t): return 'Company News'
    if re.search(r'\bats\b|applicant tracking|integration|hiring tech|hiring software|recruiting software|workflow|automation', t): return 'ATS & Hiring Tech'
    if re.search(r'\bai\b|artificial intelligence|machine learning|generative|chatbot|gpt|deepfake|fraud', t): return 'AI in Hiring'
    if re.search(r'interview|structured interview|question', t): return 'Interviewing'
    if re.search(r'employer brand|career site|candidate experience|attract', t): return 'Employer Branding'
    if re.search(r'\bdei\b|diversity|inclusion|bias|compliance|eeoc|gdpr|eu ai act|equal', t): return 'DEI & Compliance'
    if re.search(r'onboard|retention|engagement|upskill|reskill|internal mobility|develop', t): return 'Onboarding & Retention'
    if re.search(r'benchmark|report|statistic|trend|state of|survey|data reveals|index', t): return 'Industry Reports'
    if re.search(r'job description|jd template|job post|company polic|template|checklist', t): return 'Job Descriptions'
    if re.search(r'release|launch|introducing|new feature|product update|now available|update', t): return 'Product Updates'
    if re.search(r'\bhr\b|human resource|payroll|people ops|workforce planning', t): return 'HR Operations'
    return 'Hiring Strategy'

def parse_date(s):
    if not s: return None
    s = s.strip()
    for fmt in ('%a, %d %b %Y %H:%M:%S %z','%a, %d %b %Y %H:%M:%S GMT',
                '%Y-%m-%dT%H:%M:%S%z','%Y-%m-%dT%H:%M:%SZ','%Y-%m-%d'):
        try: return datetime.strptime(s[:25], fmt).strftime('%Y-%m-%d')
        except: pass
    m = re.search(r'(\d{4}-\d{2}-\d{2})', s)
    return m.group(1) if m else None

def fetch_url(url, timeout=25):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if raw[:2] == b'\x1f\x8b':
                try: raw = gzip.decompress(raw)
                except: pass
            return raw.decode('utf-8', errors='replace')
    except urllib.error.HTTPError as e:
        print(f'    ⚠ HTTP {e.code} — {url}'); return ''
    except Exception as e:
        print(f'    ⚠ {type(e).__name__} — {url}'); return ''

def dedupe(posts):
    best = {}
    for p in posts:
        u = p['u'].rstrip('/')
        p['u'] = u
        if u not in best or (p['d'] and (not best[u]['d'] or p['d'] > best[u]['d'])):
            best[u] = p
    return sorted(best.values(), key=lambda x: x['d'] or '0000', reverse=True)

# ── Parsers ───────────────────────────────────────────────────────
def parse_rss(xml_text):
    posts = []
    if not xml_text: return posts
    try:
        xml_text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', xml_text)
        root = ET.fromstring(xml_text)
        NS = 'http://www.w3.org/2005/Atom'
        items = root.findall('.//item') or root.findall(f'.//{{{NS}}}entry')
        for item in items:
            def gt(tag, ns=''):
                el = item.find(f'{{{ns}}}{tag}' if ns else tag)
                return (el.text or '').strip() if el is not None else ''
            title = gt('title') or gt('title', NS)
            link  = gt('link')
            if not link:
                el = item.find(f'{{{NS}}}link')
                link = (el.get('href','') if el is not None else '').strip()
            pub = gt('pubDate') or gt('published', NS) or gt('updated', NS)
            if link and title:
                posts.append({'u':link.strip(),'t':title.strip(),
                              'd':parse_date(pub) or TODAY,'c':guess_cat(title,link)})
    except Exception as e:
        print(f'    ⚠ RSS parse: {e}')
    return posts

def _sm_index(xml_text):
    urls=[]
    try:
        xml_text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', xml_text)
        root = ET.fromstring(xml_text)
        ns='http://www.sitemaps.org/schemas/sitemap/0.9'
        for sm in root.findall(f'{{{ns}}}sitemap') or root.findall('.//sitemap'):
            loc=(sm.findtext(f'{{{ns}}}loc') or sm.findtext('loc') or '').strip()
            if loc: urls.append(loc)
    except: pass
    return urls

def _sm_urls(xml_text, url_filter=None):
    posts=[]
    try:
        xml_text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', xml_text)
        root = ET.fromstring(xml_text)
        ns='http://www.sitemaps.org/schemas/sitemap/0.9'
        for ue in root.findall(f'{{{ns}}}url') or root.findall('.//url'):
            loc=(ue.findtext(f'{{{ns}}}loc') or ue.findtext('loc') or '').strip()
            lm =(ue.findtext(f'{{{ns}}}lastmod') or ue.findtext('lastmod') or '').strip()
            if not loc or loc.endswith('.xml'): continue
            if url_filter and not re.search(url_filter, loc): continue
            if re.search(r'/(tag|category|author|page|feed|wp-content|wp-json)/', loc): continue
            slug = loc.rstrip('/').split('/')[-1].replace('-',' ').title()
            posts.append({'u':loc,'t':slug,'d':parse_date(lm),'c':guess_cat(slug,loc)})
    except: pass
    return posts

def crawl_sitemap(sm_url, url_filter=None, max_children=40):
    txt = fetch_url(sm_url)
    if not txt: return []
    children = _sm_index(txt)
    if children:
        out=[]
        for c in children[:max_children]:
            out += _sm_urls(fetch_url(c), url_filter)
        return out
    return _sm_urls(txt, url_filter)

def collect(name, feeds, sitemaps, url_filter):
    print(f'📥 {name}...')
    posts=[]
    for f in feeds: posts += parse_rss(fetch_url(f))
    for sm in sitemaps: posts += crawl_sitemap(sm, url_filter=url_filter)
    res = dedupe(posts)
    print(f'  ✓ {len(res)} posts')
    return res

# ── Per-site fetchers ─────────────────────────────────────────────
def f_testgorilla(): return collect('TestGorilla',
    ['https://www.testgorilla.com/blog/feed/','https://www.testgorilla.com/feed/'],
    ['https://www.testgorilla.com/sitemap.xml','https://www.testgorilla.com/blog-sitemap.xml','https://www.testgorilla.com/post-sitemap.xml'],
    r'/blog/')
def f_greenhouse(): return collect('Greenhouse',
    ['https://www.greenhouse.com/blog/rss.xml','https://www.greenhouse.com/feed'],
    ['https://www.greenhouse.com/sitemap.xml','https://www.greenhouse.com/blog-sitemap.xml'],
    r'/blog/|/guidance/')
def f_lever(): return collect('Lever',
    ['https://www.lever.co/blog/feed/','https://www.lever.co/feed/'],
    ['https://www.lever.co/sitemap.xml','https://www.lever.co/post-sitemap.xml','https://www.lever.co/blog-sitemap.xml'],
    r'/blog/')
def f_workable(): return collect('Workable',
    ['https://resources.workable.com/feed/','https://resources.workable.com/comments/feed/'],
    ['https://resources.workable.com/wp-sitemap.xml','https://resources.workable.com/sitemap.xml','https://resources.workable.com/post-sitemap.xml'],
    r'resources\.workable\.com/(?!tag|category|author)')
def f_indeed(): return collect('Indeed Hire',
    [],
    ['https://www.indeed.com/hire/sitemap.xml','https://www.indeed.com/sitemap.xml'],
    r'/hire/resources/')
def f_linkedin(): return collect('LinkedIn Talent',
    ['https://www.linkedin.com/business/talent/blog/feed'],
    [],
    r'/talent/blog/')
def f_hackerrank(): return collect('HackerRank',
    ['https://www.hackerrank.com/blog/feed/','https://blog.hackerrank.com/feed/'],
    ['https://www.hackerrank.com/sitemap.xml','https://www.hackerrank.com/blog/sitemap.xml'],
    r'/blog/')
def f_hackerearth(): return collect('HackerEarth',
    ['https://www.hackerearth.com/blog/feed/'],
    ['https://www.hackerearth.com/sitemap.xml','https://www.hackerearth.com/blog/sitemap.xml'],
    r'/blog/')
def f_imocha(): return collect('iMocha',
    ['https://blog.imocha.io/rss.xml','https://www.imocha.io/blog/rss.xml'],
    ['https://www.imocha.io/sitemap.xml','https://blog.imocha.io/sitemap.xml'],
    r'/blog')

FETCHERS = {
    'testgorilla':f_testgorilla,'greenhouse':f_greenhouse,'lever':f_lever,
    'workable':f_workable,'indeed':f_indeed,'linkedin':f_linkedin,
    'hackerrank':f_hackerrank,'hackerearth':f_hackerearth,'imocha':f_imocha,
}
SITE_ORDER = ['testgorilla','greenhouse','lever','workable','indeed',
              'linkedin','hackerrank','hackerearth','imocha']

# Static fallback = the seed posts already in index.html (extracted at runtime),
# so a thin live fetch never wipes good data. Populated from current HTML below.

# ── Inject ────────────────────────────────────────────────────────
def js_array(posts):
    def esc(s): return (s or '').replace('\\','\\\\').replace('"','\\"')
    items = ',\n'.join('{u:"%s",t:"%s",d:"%s",c:"%s"}' %
                       (esc(p['u']), esc(p['t']), p['d'] or TODAY, esc(p['c'])) for p in posts)
    return '[\n' + items + '\n]'

def existing_count(html, key):
    # match the same boundaries inject() uses: "...],\nnextkey" or "...]\n};"
    m = re.search(rf'\b{re.escape(key)}:\[(.*?)\]\s*(?:,\s*\n\s*\w+:\[|\n\s*\}};)', html, re.DOTALL)
    return len(re.findall(r'\{u:', m.group(1))) if m else 0

def inject(all_data):
    if not os.path.exists(HTML_FILE):
        print(f'❌ {HTML_FILE} not found'); return
    with open(HTML_FILE, encoding='utf-8') as f: html = f.read()

    for i, key in enumerate(SITE_ORDER):
        posts = all_data.get(key, [])
        ex = existing_count(html, key)
        MIN = 3 if key in ('linkedin','hackerrank') else 30
        if len(posts) < MIN and len(posts) < ex:
            print(f'  ↩ {key}: live={len(posts)} too thin — keeping existing ({ex})')
            continue
        if not posts:
            print(f'  ⚠ {key}: 0 posts — keeping existing'); continue
        arr = js_array(posts)
        if i + 1 < len(SITE_ORDER):
            nxt = SITE_ORDER[i+1]
            pat = rf'({re.escape(key)}:\[)(.*?)(\]\s*,\s*\n\s*{re.escape(nxt)})'
            rep = rf'{key}:{arr},\n{nxt}'
        else:
            pat = rf'({re.escape(key)}:\[)(.*?)(\]\s*\n\s*\}};)'
            rep = rf'{key}:{arr}\n}};'
        new, n = re.subn(pat, rep, html, count=1, flags=re.DOTALL)
        if n: html = new; print(f'  ✓ {key}: {len(posts)} posts injected')
        else: print(f'  ⚠ {key}: regex miss — kept existing')

    # pill counts
    for key, posts in all_data.items():
        if posts:
            html = re.sub(rf'(data-k="{re.escape(key)}".*?site-pill-url[^>]*>)\d+',
                          rf'\g<1>{len(posts)}', html, count=1, flags=re.DOTALL)
    html = re.sub(r"const TODAY='[\d-]+'", f"const TODAY='{TODAY}'", html)

    with open(HTML_FILE, 'w', encoding='utf-8') as f: f.write(html)
    print(f"\n✅ index.html updated — total {sum(len(v) for v in all_data.values())} posts")

def main():
    print(f'🚀 B2B BlogIntel — {TODAY}\n')
    data = {k: FETCHERS[k]() for k in SITE_ORDER}
    print('\n📊 Summary:')
    for k in SITE_ORDER: print(f'   {k}: {len(data[k])} posts')
    print('\n💉 Injecting...')
    inject(data)

if __name__ == '__main__':
    main()
