#!/usr/bin/env python3
"""
Balanced News Agent
-------------------
1. Collects recent headlines from reputable publishers (RSS feeds).
2. Asks Google's Gemini (free tier) to group them into the day's top stories.
3. For each story, writes a detailed brief with arguments on both sides,
   links to the original articles, and links to related past stories.
4. Builds a static website in the "docs" folder (hosted free on GitHub Pages).

Run:   python newsagent.py          (needs GEMINI_API_KEY)
Test:  python newsagent.py --demo   (no key needed, writes to "preview")
"""
import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import requests

# ----------------------------------------------------------------------------
# SETTINGS - safe to edit
# ----------------------------------------------------------------------------
# Models are tried in this order; the first one your key can use is kept.
# (Google renames models often. Set a GEMINI_MODEL secret/variable to force one.)
MODELS = (
    [os.environ["GEMINI_MODEL"]]
    if os.environ.get("GEMINI_MODEL")
    else ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.6-flash"]
)
_model_index = [0]
API_KEY = os.environ.get("GEMINI_API_KEY", "")
# For each section: (full balanced briefs, extra one-line headlines).
# Each full brief costs one AI request; one-line headlines are free.
QUOTA = {
    "World": (4, 6),
    "India": (3, 5),
    "Politics": (3, 4),
    "Business": (3, 5),
    "Tech": (3, 5),
    "Science": (2, 4),
    "Health": (2, 3),
    "Environment": (2, 3),
    "Sports": (2, 4),
    "Entertainment": (2, 4),
}
CATEGORIES = list(QUOTA)
HOURS_BACK = 36            # only consider articles newer than this
PER_FEED = 12              # max articles taken from each feed
ARCHIVE_DAYS = 90          # how far back "related past stories" can look
USE_FULL_TEXT = os.environ.get("FULL_TEXT", "1") == "1"  # try reading article pages
SECONDS_BETWEEN_AI_CALLS = 7   # keeps us inside the free-tier rate limit
SITE_TITLE = "Balanced Briefing"

# (publisher, category, feed URL). Add or remove lines freely.
# If a feed is down or has moved, it is simply skipped.
FEEDS = [
    # World
    ("BBC News", "World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("The Guardian", "World", "https://www.theguardian.com/world/rss"),
    ("Al Jazeera", "World", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("NPR", "World", "https://feeds.npr.org/1004/rss.xml"),
    ("DW", "World", "https://rss.dw.com/rdf/rss-en-all"),
    ("The New York Times", "World", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
    ("The Hindu", "World", "https://www.thehindu.com/news/international/feeder/default.rss"),
    ("FRANCE 24", "World", "https://www.france24.com/en/rss"),
    # India
    ("The Hindu", "India", "https://www.thehindu.com/news/national/feeder/default.rss"),
    ("The Indian Express", "India", "https://indianexpress.com/section/india/feed/"),
    ("Times of India", "India", "https://timesofindia.indiatimes.com/rssfeedstopstories.cms"),
    ("NDTV", "India", "https://feeds.feedburner.com/ndtvnews-top-stories"),
    ("Hindustan Times", "India", "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml"),
    # Politics (US and global politics)
    ("BBC News", "Politics", "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"),
    ("NPR", "Politics", "https://feeds.npr.org/1014/rss.xml"),
    ("The Guardian", "Politics", "https://www.theguardian.com/us-news/us-politics/rss"),
    ("The New York Times", "Politics", "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml"),
    ("The Guardian", "Politics", "https://www.theguardian.com/politics/rss"),
    # Business
    ("BBC News", "Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("The Guardian", "Business", "https://www.theguardian.com/uk/business/rss"),
    ("CNBC", "Business", "https://www.cnbc.com/id/10001147/device/rss/rss.html"),
    ("The New York Times", "Business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("The Hindu BusinessLine", "Business", "https://www.thehindubusinessline.com/feeder/default.rss"),
    ("The Economic Times", "Business", "https://economictimes.indiatimes.com/rssfeedsdefault.cms"),
    # Tech
    ("BBC News", "Tech", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("The Guardian", "Tech", "https://www.theguardian.com/technology/rss"),
    ("Ars Technica", "Tech", "https://feeds.arstechnica.com/arstechnica/index"),
    ("The Verge", "Tech", "https://www.theverge.com/rss/index.xml"),
    ("WIRED", "Tech", "https://www.wired.com/feed/rss"),
    ("TechCrunch", "Tech", "https://techcrunch.com/feed/"),
    ("MIT Technology Review", "Tech", "https://www.technologyreview.com/feed/"),
    # Science
    ("BBC News", "Science", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"),
    ("The New York Times", "Science", "https://rss.nytimes.com/services/xml/rss/nyt/Science.xml"),
    ("The Guardian", "Science", "https://www.theguardian.com/science/rss"),
    ("NPR", "Science", "https://feeds.npr.org/1007/rss.xml"),
    ("Nature", "Science", "https://www.nature.com/nature.rss"),
    # Health
    ("BBC News", "Health", "https://feeds.bbci.co.uk/news/health/rss.xml"),
    ("The New York Times", "Health", "https://rss.nytimes.com/services/xml/rss/nyt/Health.xml"),
    ("NPR", "Health", "https://feeds.npr.org/1128/rss.xml"),
    ("STAT", "Health", "https://www.statnews.com/feed/"),
    ("The Guardian", "Health", "https://www.theguardian.com/society/health/rss"),
    # Environment and climate
    ("The Guardian", "Environment", "https://www.theguardian.com/environment/rss"),
    ("The New York Times", "Environment", "https://rss.nytimes.com/services/xml/rss/nyt/Climate.xml"),
    ("Carbon Brief", "Environment", "https://www.carbonbrief.org/feed/"),
    ("Mongabay", "Environment", "https://news.mongabay.com/feed/"),
    # Sports
    ("BBC Sport", "Sports", "https://feeds.bbci.co.uk/sport/rss.xml"),
    ("The Guardian", "Sports", "https://www.theguardian.com/sport/rss"),
    ("ESPN", "Sports", "https://www.espn.com/espn/rss/news"),
    ("The Hindu", "Sports", "https://www.thehindu.com/sport/feeder/default.rss"),
    # Entertainment and culture
    ("BBC News", "Entertainment", "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml"),
    ("The Guardian", "Entertainment", "https://www.theguardian.com/culture/rss"),
    ("The New York Times", "Entertainment", "https://rss.nytimes.com/services/xml/rss/nyt/Arts.xml"),
    ("Variety", "Entertainment", "https://variety.com/feed/"),
]

ROOT = os.path.dirname(os.path.abspath(__file__))
IST = timezone(timedelta(hours=5, minutes=30))
UA = "Mozilla/5.0 (compatible; BalancedBriefingBot/1.0)"
esc = html.escape


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


STOP = set(
    "the a an of to in on for and or with at by from as is are was were be this that it its after "
    "over new says say will not has have had into about up out than more their his her us how why "
    "what who when could would".split()
)


def tokens(text):
    return {w for w in re.findall(r"[a-z0-9]{3,}", text.lower()) if w not in STOP}


def related_candidates(text, archive, n=12):
    """Pick past stories that share several keywords with this story."""
    t = tokens(text)
    scored = []
    for a in archive:
        score = len(t & tokens(a["headline"] + " " + a.get("blurb", "")))
        if score >= 2:
            scored.append((score, a))
    scored.sort(key=lambda x: -x[0])
    return [a for _, a in scored[:n]]


# ----------------------------------------------------------------------------
# Step 1: collect articles
# ----------------------------------------------------------------------------
def fetch_articles():
    import feedparser

    cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)
    articles = []
    for name, cat, url in FEEDS:
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
        except Exception as e:  # feed down, blocked, moved...
            print(f"  skipped {name} ({cat}): {e}")
            continue
        count = 0
        for e in feed.entries:
            if count >= PER_FEED:
                break
            link, title = e.get("link", ""), clean(e.get("title", ""))
            if not link.startswith("http") or not title:
                continue
            ts = e.get("published_parsed") or e.get("updated_parsed")
            if ts and datetime(*ts[:6], tzinfo=timezone.utc) < cutoff:
                continue
            articles.append(
                {
                    "source": name,
                    "category": cat,
                    "title": title,
                    "url": link,
                    "summary": clean(e.get("summary", ""))[:500],
                }
            )
            count += 1
        print(f"  {name} ({cat}): {count} articles")
    return articles


def read_full_text(url, limit=3000):
    """Best effort: read the article page. Paywalled/blocked pages just return ''."""
    try:
        import trafilatura

        r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        if r.status_code != 200:
            return ""
        return (trafilatura.extract(r.text) or "")[:limit]
    except Exception:
        return ""


# ----------------------------------------------------------------------------
# Step 2: talk to Gemini
# ----------------------------------------------------------------------------
_last_call = [0.0]


def gemini_json(prompt):
    wait = SECONDS_BETWEEN_AI_CALLS - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3},
    }
    attempt = 0
    while attempt < 4:
        model = MODELS[_model_index[0]]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        _last_call[0] = time.time()
        try:
            r = requests.post(url, headers={"x-goog-api-key": API_KEY}, json=body, timeout=120)
        except requests.exceptions.RequestException as e:
            # Slow or dropped connection: retry, and after two failures switch to the next (faster) model
            attempt += 1
            print(f"  {model} timed out or dropped ({type(e).__name__}); attempt {attempt}")
            if attempt >= 2 and _model_index[0] + 1 < len(MODELS):
                _model_index[0] += 1
                attempt = 0
                print(f"  switching to {MODELS[_model_index[0]]}")
            time.sleep(5)
            continue
        if r.status_code in (404, 403) and _model_index[0] + 1 < len(MODELS):
            print(f"  model {model} not available to this key ({r.status_code}); trying {MODELS[_model_index[0] + 1]}")
            _model_index[0] += 1
            continue
        if r.status_code in (429, 500, 503):
            attempt += 1
            time.sleep(25 * attempt)
            continue
        if not r.ok:
            raise RuntimeError(f"Gemini error {r.status_code} for model {model}: {r.text[:400]}")
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    raise RuntimeError("Gemini kept failing or rate-limiting")


def pick_stories(category, arts, n_total):
    lines = "\n".join(
        f"[{i}] {a['source']}: {a['title']} - {a['summary'][:90]}" for i, a in enumerate(arts)
    )
    prompt = f"""You are the editor of a balanced daily news digest. Below are recent headlines from reputable publishers in the "{category}" section.
Group articles about the same event into stories, then choose the {n_total} most important and newsworthy stories, ordered from most to least important.

Rules:
- Prefer stories covered by several different publishers and with real consequences for people.
- Each story lists the numbers of the articles covering it (1 to 8 numbers).
- Skip advertorials, shopping guides, listicles, quizzes, horoscopes and pure opinion pieces.
- "blurb" is one neutral sentence (max 30 words) saying what happened, using only facts from the headlines.

Return JSON exactly like:
{{"stories":[{{"headline":"short neutral headline","blurb":"one neutral sentence","article_ids":[0,1]}}]}}

ARTICLES:
{lines}"""
    return gemini_json(prompt).get("stories", [])


def write_story(headline, category, arts, past):
    blocks = []
    for n, a in enumerate(arts, 1):
        body = read_full_text(a["url"]) if (USE_FULL_TEXT and n <= 3) else ""
        text = body or a["summary"]
        blocks.append(f"SOURCE {n}: {a['source']}\nTitle: {a['title']}\nText: {text}")
    past_lines = "\n".join(f"{p['id']} | {p['date']} | {p['headline']}" for p in past) or "(none)"

    prompt = f"""You are a careful, neutral news analyst. Write one entry for a balanced news digest about this story: "{headline}".

Use ONLY facts found in the sources below. Do not invent facts, numbers, names or quotes. If the sources are thin, say so plainly.
Use calm, neutral wording. Do not take a side. Present the strongest version of each side's argument.

Return JSON with exactly these keys:
- "headline": neutral headline (no clickbait)
- "summary": a detailed explanation of what happened and why it matters, 3 to 5 short paragraphs separated by \\n\\n (about 250-400 words if the sources allow)
- "key_facts": list of 3 to 6 short factual bullet strings
- "case_for": list of 1 to 4 objects {{"point": "...", "sources": [source numbers]}} - arguments in favour, supporters' views, or positive angles/benefits
- "case_against": list of 1 to 4 objects {{"point": "...", "sources": [source numbers]}} - criticism, opposing views, risks or concerns. If the story is a plain event with no real sides, use these two lists for the different reactions or interpretations instead.
- "where_sources_differ": one or two sentences on how the publishers' coverage or emphasis differs (empty string if they agree)
- "background_topics": up to 3 short names of earlier events, laws, companies or topics a reader should know about for context (each suitable as a Wikipedia article title)
- "related_past_ids": ids from the PAST STORIES list that are genuinely about the same ongoing story (empty list if none)

PAST STORIES (id | date | headline):
{past_lines}

SOURCES:
{chr(10).join(blocks)}"""
    return gemini_json(prompt)


# ----------------------------------------------------------------------------
# Step 3: assemble stories
# ----------------------------------------------------------------------------
def load_archive(data_dir, exclude_date):
    cutoff = (datetime.now(IST) - timedelta(days=ARCHIVE_DAYS)).strftime("%Y-%m-%d")
    archive = []
    for fn in sorted(os.listdir(data_dir)):
        if not fn.endswith(".json"):
            continue
        d = fn[:-5]
        if d == exclude_date or d < cutoff:
            continue
        with open(os.path.join(data_dir, fn), encoding="utf-8") as f:
            day = json.load(f)
        for s in day.get("stories", []):
            archive.append(
                {"id": s["id"], "date": d, "headline": s["headline"], "blurb": s["summary"][:200]}
            )
    return archive


def clean_points(points, n_sources):
    out = []
    for p in points or []:
        if not isinstance(p, dict) or not p.get("point"):
            continue
        refs = [int(x) for x in p.get("sources", []) if str(x).isdigit() and 1 <= int(x) <= n_sources]
        out.append({"point": str(p["point"]), "sources": refs})
    return out[:4]


def is_duplicate(headline, seen_heads):
    t = tokens(headline)
    for h in seen_heads:
        u = tokens(h)
        if t and u and len(t & u) / len(t | u) >= 0.6:
            return True
    return False


def make_story(today, category, headline, arts, archive):
    candidates = related_candidates(headline + " " + " ".join(a["title"] for a in arts), archive)
    w = write_story(headline, category, arts, candidates)
    sources = [{"name": a["source"], "title": a["title"], "url": a["url"]} for a in arts]
    valid_past = {p["id"]: p for p in candidates}
    related = [
        {"id": i, "date": valid_past[i]["date"], "headline": valid_past[i]["headline"]}
        for i in w.get("related_past_ids", [])
        if i in valid_past
    ][:4]
    background = [
        {"title": t, "url": "https://en.wikipedia.org/w/index.php?search=" + quote_plus(t)}
        for t in w.get("background_topics", [])[:3]
        if isinstance(t, str) and t.strip()
    ]
    return {
        "id": hashlib.md5((today + category + headline).encode()).hexdigest()[:8],
        "category": category,
        "headline": str(w.get("headline") or headline),
        "summary": str(w.get("summary", "")),
        "key_facts": [str(x) for x in w.get("key_facts", [])][:6],
        "case_for": clean_points(w.get("case_for"), len(sources)),
        "case_against": clean_points(w.get("case_against"), len(sources)),
        "differ": str(w.get("where_sources_differ", "")),
        "background": background,
        "related": related,
        "sources": sources,
    }


def build_stories(today, data_dir):
    print("Collecting articles...")
    articles = fetch_articles()
    if len(articles) < 10:
        sys.exit("Too few articles were collected (network problem?). Leaving the site unchanged.")
    print(f"Collected {len(articles)} articles.")
    archive = load_archive(data_dir, today)

    stories, quick = [], []
    seen_urls, seen_heads = set(), []
    for cat in CATEGORIES:
        full_n, quick_n = QUOTA[cat]
        arts = [a for a in articles if a["category"] == cat]
        if len(arts) < 3:
            print(f"Skipping {cat}: too few articles")
            continue
        print(f"== {cat}: choosing stories from {len(arts)} articles")
        try:
            picks = pick_stories(cat, arts, full_n + quick_n)
        except Exception as e:
            print(f"  could not choose {cat} stories, skipping: {e}")
            continue
        rank = 0
        for pick in picks:
            ids = [i for i in pick.get("article_ids", []) if isinstance(i, int) and 0 <= i < len(arts)]
            sel = [arts[i] for i in dict.fromkeys(ids)][:6]
            if not sel:
                continue
            headline = str(pick.get("headline") or sel[0]["title"])
            urls = {a["url"] for a in sel}
            if urls & seen_urls or is_duplicate(headline, seen_heads):
                continue
            seen_urls |= urls
            seen_heads.append(headline)
            rank += 1
            if rank > full_n + quick_n:
                break
            as_quick = {
                "category": cat,
                "headline": headline,
                "blurb": str(pick.get("blurb", "")),
                "sources": [{"name": a["source"], "title": a["title"], "url": a["url"]} for a in sel[:4]],
            }
            if rank <= full_n:
                print(f"  writing: {headline}")
                try:
                    stories.append(make_story(today, cat, headline, sel, archive))
                except Exception as e:
                    print(f"    failed, listing as a headline instead: {e}")
                    quick.append(as_quick)
            else:
                quick.append(as_quick)
    return stories, quick


# ----------------------------------------------------------------------------
# Step 4: build the website
# ----------------------------------------------------------------------------
CSS = """
:root{--bg:#fafaf7;--card:#fff;--ink:#1c1c1a;--mute:#666;--line:#e4e2da;--accent:#1f4e8c;--for:#1c7a4a;--against:#a3341f}
@media(prefers-color-scheme:dark){:root{--bg:#141413;--card:#1e1e1c;--ink:#ecebe6;--mute:#9a988f;--line:#333330;--accent:#7fb0f0;--for:#5cc58d;--against:#f08a75}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.6 Georgia,'Times New Roman',serif}
.wrap{max-width:760px;margin:0 auto;padding:0 16px 60px}
header{padding:28px 0 8px}h1{font:700 28px/1.2 system-ui,sans-serif;margin:0}
.sub{color:var(--mute);font:14px system-ui,sans-serif;margin-top:4px}
nav.days,.filters{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0;font:14px system-ui,sans-serif}
nav.days a,.filters button{padding:5px 12px;border:1px solid var(--line);border-radius:999px;color:var(--ink);text-decoration:none;background:var(--card);cursor:pointer;font:inherit}
.filters button.on,nav.days a.on{background:var(--accent);color:#fff;border-color:var(--accent)}
article{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px;margin:18px 0}
.tag{font:600 12px system-ui,sans-serif;letter-spacing:.06em;text-transform:uppercase;color:var(--accent)}
article h2{font:700 22px/1.3 system-ui,sans-serif;margin:6px 0 12px}
article p{margin:0 0 12px}h3{font:600 14px system-ui,sans-serif;text-transform:uppercase;letter-spacing:.05em;color:var(--mute);margin:20px 0 6px}
ul{padding-left:20px;margin:0}li{margin-bottom:8px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:620px){.cols{grid-template-columns:1fr}}
.for h3{color:var(--for)}.against h3{color:var(--against)}
.ref{font:12px system-ui,sans-serif;color:var(--accent);margin-right:6px;white-space:nowrap}
a{color:var(--accent)}.small{font:14px system-ui,sans-serif;color:var(--mute)}
.pill{display:inline-block;margin:0 8px 6px 0;font:14px system-ui,sans-serif}
.quick li{margin-bottom:10px;font:16px/1.5 system-ui,sans-serif}.quick h3{margin-top:4px}
footer{color:var(--mute);font:13px/1.5 system-ui,sans-serif;margin-top:40px}
.demo{background:#fff3cd;color:#664d03;padding:10px 14px;border-radius:8px;font:14px system-ui,sans-serif;margin-top:12px}
"""

JS = """
document.querySelectorAll('.filters button').forEach(function(b){b.addEventListener('click',function(){
document.querySelectorAll('.filters button').forEach(function(x){x.classList.remove('on')});b.classList.add('on');
var c=b.dataset.c;document.querySelectorAll('article').forEach(function(a){a.style.display=(c==='All'||a.dataset.c===c)?'':'none'})})});
"""


def refs(points_refs, sources):
    return " ".join(
        f'<a class="ref" href="{esc(sources[n-1]["url"])}" target="_blank" rel="noopener">{esc(sources[n-1]["name"])}</a>'
        for n in points_refs
    )


def render_points(points, sources):
    if not points:
        return "<p class='small'>Nothing significant reported.</p>"
    items = "".join(f"<li>{esc(p['point'])} {refs(p['sources'], sources)}</li>" for p in points)
    return f"<ul>{items}</ul>"


def render_quick(cat, items):
    if not items:
        return ""
    lis = ""
    for q in items:
        links = " ".join(
            f'<a class="ref" href="{esc(x["url"])}" target="_blank" rel="noopener">{esc(x["name"])}</a>'
            for x in q["sources"]
        )
        lis += f"<li><b>{esc(q['headline'])}.</b> {esc(q['blurb'])} {links}</li>"
    return (
        f'<article class="quick" data-c="{esc(cat)}"><div class="tag">{esc(cat)}</div>'
        f"<h3>More {esc(cat)} headlines</h3><ul>{lis}</ul></article>"
    )


def render_story(s):
    src = s["sources"]
    paras = "".join(f"<p>{esc(p.strip())}</p>" for p in s["summary"].split("\n") if p.strip())
    facts = "".join(f"<li>{esc(f)}</li>" for f in s["key_facts"])
    out = [f'<article id="{esc(s["id"])}" data-c="{esc(s["category"])}">']
    out.append(f'<div class="tag">{esc(s["category"])}</div><h2>{esc(s["headline"])}</h2>{paras}')
    if facts:
        out.append(f"<h3>Key facts</h3><ul>{facts}</ul>")
    out.append('<div class="cols">')
    out.append(f'<div class="for"><h3>In favour / positives</h3>{render_points(s["case_for"], src)}</div>')
    out.append(f'<div class="against"><h3>Against / concerns</h3>{render_points(s["case_against"], src)}</div>')
    out.append("</div>")
    if s["differ"]:
        out.append(f"<h3>Where coverage differs</h3><p>{esc(s['differ'])}</p>")
    if s["related"]:
        links = "".join(
            f'<span class="pill">&#8627; <a href="{esc(r["date"])}.html#{esc(r["id"])}">{esc(r["headline"])}</a> <span class="small">({esc(r["date"])})</span></span>'
            for r in s["related"]
        )
        out.append(f"<h3>Earlier coverage in this digest</h3>{links}")
    if s["background"]:
        links = "".join(
            f'<span class="pill"><a href="{esc(b["url"])}" target="_blank" rel="noopener">{esc(b["title"])}</a></span>'
            for b in s["background"]
        )
        out.append(f"<h3>Background reading (Wikipedia)</h3>{links}")
    srcs = "".join(
        f'<li><a href="{esc(x["url"])}" target="_blank" rel="noopener">{esc(x["title"])}</a> <span class="small">- {esc(x["name"])}</span></li>'
        for x in src
    )
    out.append(f"<h3>Read the original reporting</h3><ul>{srcs}</ul></article>")
    return "".join(out)


def render_page(day, dates, demo):
    d = day["date"]
    nav = "".join(
        f'<a href="{x}.html"{" class=on" if x == d else ""}>{x}</a>' for x in dates[:14]
    )
    quick = day.get("quick", [])
    present = {s["category"] for s in day["stories"]} | {q["category"] for q in quick}
    ordered = [c for c in CATEGORIES if c in present] + sorted(present - set(CATEGORIES))
    cats = ["All"] + ordered
    filters = "".join(
        f'<button data-c="{esc(c)}"{" class=on" if c == "All" else ""}>{esc(c)}</button>' for c in cats
    )
    banner = '<div class="demo">SAMPLE DATA - this is a demo page, not real news.</div>' if demo else ""
    body = ""
    for c in ordered:
        body += "".join(render_story(s) for s in day["stories"] if s["category"] == c)
        body += render_quick(c, [q for q in quick if q["category"] == c])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(SITE_TITLE)} - {esc(d)}</title><link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icon.svg"><meta name="theme-color" content="#1f4e8c"><style>{CSS}</style></head>
<body><div class="wrap"><header><h1>{esc(SITE_TITLE)}</h1>
<div class="sub">Digest for {esc(d)} &middot; updated {esc(day.get("generated_at", ""))}</div>{banner}</header>
<nav class="days">{nav}</nav><div class="filters">{filters}</div>{body}
<footer>Summaries are written by an AI from the linked publishers' reporting and can contain mistakes.
Always check the original articles for anything important. Sources are chosen to span several outlets,
but no selection of sources is perfectly neutral.</footer></div><script>{JS}</script></body></html>"""


ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#1f4e8c"/><path d="M16 20h32M16 32h32M16 44h20" stroke="#fff" stroke-width="5" stroke-linecap="round"/></svg>'
MANIFEST = {
    "name": SITE_TITLE,
    "short_name": "Briefing",
    "start_url": "./",
    "display": "standalone",
    "background_color": "#fafaf7",
    "theme_color": "#1f4e8c",
    "icons": [{"src": "icon.svg", "sizes": "any", "type": "image/svg+xml"}],
}


def render_all(out_dir, demo=False):
    data_dir = os.path.join(out_dir, "data")
    files = sorted((f for f in os.listdir(data_dir) if f.endswith(".json")), reverse=True)
    dates = [f[:-5] for f in files]
    for i, f in enumerate(files):
        with open(os.path.join(data_dir, f), encoding="utf-8") as fh:
            day = json.load(fh)
        page = render_page(day, dates, demo)
        with open(os.path.join(out_dir, f"{f[:-5]}.html"), "w", encoding="utf-8") as fh:
            fh.write(page)
        if i == 0:  # newest day is the home page
            with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as fh:
                fh.write(page)
    with open(os.path.join(out_dir, "icon.svg"), "w") as fh:
        fh.write(ICON)
    with open(os.path.join(out_dir, "manifest.webmanifest"), "w") as fh:
        json.dump(MANIFEST, fh)


# ----------------------------------------------------------------------------
# Demo data (so you can preview the site without an API key)
# ----------------------------------------------------------------------------
def demo_day(date, headline, sid):
    return {
        "date": date,
        "generated_at": "demo",
        "stories": [
            {
                "id": sid,
                "category": "Tech",
                "headline": headline,
                "summary": "This is placeholder text showing how a real entry will look.\n\nA real entry has several paragraphs written from the linked reporting.",
                "key_facts": ["Placeholder fact one", "Placeholder fact two"],
                "case_for": [{"point": "Supporters argue this could lower costs.", "sources": [1]}],
                "case_against": [{"point": "Critics worry about privacy and safety.", "sources": [2]}],
                "differ": "One outlet stresses economics while another stresses regulation.",
                "background": [{"title": "Example topic", "url": "https://en.wikipedia.org/w/index.php?search=Example+topic"}],
                "related": [],
                "sources": [
                    {"name": "Publisher A", "title": "Example article title", "url": "https://example.com/a"},
                    {"name": "Publisher B", "title": "Another example article", "url": "https://example.com/b"},
                ],
            }
        ],
    }


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="build a sample site in ./preview, no API key needed")
    args = ap.parse_args()

    out_dir = os.path.join(ROOT, "preview" if args.demo else "docs")
    data_dir = os.path.join(out_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    now = datetime.now(IST)
    today = now.strftime("%Y-%m-%d")

    if args.demo:
        yday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        old = demo_day(yday, "Earlier story: chipmaker unveils new processor", "demo0001")
        new = demo_day(today, "Example: governments debate new AI rules", "demo0002")
        new["stories"][0]["related"] = [{"id": "demo0001", "date": yday, "headline": old["stories"][0]["headline"]}]
        new["stories"].append({**demo_day(today, "Example: central bank holds interest rates", "demo0003")["stories"][0], "category": "Business"})
        new["quick"] = [
            {
                "category": "Sports",
                "headline": "Example: league final goes to extra time",
                "blurb": "A placeholder one-line headline summary shows here.",
                "sources": [{"name": "Publisher A", "title": "Example", "url": "https://example.com/c"}],
            }
        ]
        for day in (old, new):
            with open(os.path.join(data_dir, day["date"] + ".json"), "w", encoding="utf-8") as f:
                json.dump(day, f)
        render_all(out_dir, demo=True)
        print("Demo site written to", out_dir)
        return

    if not API_KEY:
        sys.exit("GEMINI_API_KEY is not set. See README.md, step 1 and step 4.")
    stories, quick = build_stories(today, data_dir)
    if not stories and not quick:
        sys.exit("No stories were produced. Leaving the site unchanged.")
    day = {"date": today, "generated_at": now.strftime("%H:%M IST"), "stories": stories, "quick": quick}
    with open(os.path.join(data_dir, today + ".json"), "w", encoding="utf-8") as f:
        json.dump(day, f, ensure_ascii=False, indent=1)
    render_all(out_dir)
    print(f"Done: {len(stories)} full briefs and {len(quick)} headlines written to {out_dir}")


if __name__ == "__main__":
    main()
