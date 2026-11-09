"""Stage 1a: keyword collection from RSS feeds and Google News RSS. No AI."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import quote, urlparse, urlunparse, parse_qsl, urlencode

import feedparser

from .matching import is_on_topic

TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                   "ref", "fbclid", "gclid", "oc", "guccounter"}


@dataclass
class Item:
    title: str
    url: str
    source: str               # publisher or feed name
    found_by: set             # {"rss", "google_keywords", "google_site", "google_firm"}
    queries: list = field(default_factory=list)
    rss_date: Optional[datetime] = None
    snippet: str = ""
    google_url: str = ""      # original news.google.com link, if any
    also: list = field(default_factory=list)  # other (source, url) covering the same story
    extra_text: str = ""   # titles/snippets of merged duplicates
    page_text: str = ""    # start of the article page, when opened
    # filled later
    date: Optional[datetime] = None
    date_method: str = ""
    dates_found: dict = field(default_factory=dict)
    date_verified: bool = True
    date_note: str = ""
    prescore: int = 0
    prescore_reasons: list = field(default_factory=list)
    status: str = ""          # kept / unverified / dropped:<reason>

    def text(self) -> str:
        return f"{self.title}\n{self.snippet}\n{self.extra_text}\n{self.page_text}"


def canonical_url(url: str) -> str:
    try:
        p = urlparse(url.strip())
    except ValueError:
        return url
    q = [(k, v) for k, v in parse_qsl(p.query) if k.lower() not in TRACKING_PARAMS]
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme.lower() or "https", p.netloc.lower().removeprefix("www."),
                       path, "", urlencode(q), ""))


def _entry_date(e) -> Optional[datetime]:
    for key in ("published_parsed", "updated_parsed"):
        # "updated" is only a fallback when the feed has no published date;
        # the page-level check later prefers the original publication date.
        t = e.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def _clean_snippet(html: str) -> str:
    from bs4 import BeautifulSoup
    return BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)[:600]


def google_news_url(query: str, edition: dict) -> str:
    return (f"https://news.google.com/rss/search?q={quote(query)}"
            f"&hl={edition['hl']}&gl={edition['gl']}&ceid={quote(edition['ceid'])}")


def parse_feed(content: bytes, feed_name: str, found_by: str, query: str = "") -> list[Item]:
    parsed = feedparser.parse(content)
    items = []
    for e in parsed.entries:
        link = e.get("link", "")
        title = (e.get("title") or "").strip()
        if not link or not title:
            continue
        source = feed_name
        google_url = link if "news.google.com" in link else ""
        src = e.get("source") or {}
        if src.get("title"):
            # Aggregator feeds (Google News) name the publisher and append
            # " - Publisher" to every title; strip it.
            source = src["title"]
            if title.endswith(f" - {source}"):
                title = title[: -len(source) - 3].strip()
        items.append(Item(title=title, url=link, source=source, found_by={found_by},
                          queries=[query] if query else [], rss_date=_entry_date(e),
                          snippet=_clean_snippet(e.get("summary", "")), google_url=google_url))
    return items


def build_requests(sources: dict, keywords: dict, watchlist: dict, settings: dict,
                   when: str) -> list[dict]:
    """Every feed request the run will make, with how to label its results."""
    reqs = []
    for s in sources.get("rss", []):
        reqs.append({"name": s["name"], "url": s["url"], "found_by": "rss",
                     "filter": s.get("filter", "keywords"), "query": ""})
    editions = settings.get("google_news_editions", [])
    for q in keywords.get("google_news_queries", []):
        for ed in editions:
            full = f"{q} when:{when}"
            reqs.append({"name": f"Google News [{ed['gl']}]: {q}", "url": google_news_url(full, ed),
                         "found_by": "google_keywords", "filter": "keywords", "query": q})
    context = "(tokenization OR tokenisation OR tokenized OR tokenised OR RWA OR onchain OR stablecoin OR blockchain OR AI)"
    seen_names = set()
    for f in watchlist.get("firms", []):
        if f["name"] in seen_names:
            continue
        seen_names.add(f["name"])
        q = f'"{f["name"]}" {context} when:{when}'
        ed = editions[0] if editions else {"hl": "en-US", "gl": "US", "ceid": "US:en"}
        reqs.append({"name": f"Google News firm: {f['name']}", "url": google_news_url(q, ed),
                     "found_by": "google_firm", "filter": "keywords", "query": f["name"]})
    for s in sources.get("google_news_site_queries", []):
        q = f"site:{s['site']} when:{when}"
        ed = editions[0] if editions else {"hl": "en-US", "gl": "US", "ceid": "US:en"}
        reqs.append({"name": f"Google News site: {s['name']}", "url": google_news_url(q, ed),
                     "found_by": "google_site", "filter": "keywords", "query": s["site"]})
    return reqs


def collect(reqs: list[dict], fetch: Callable[[str], bytes], keywords: dict, watchlist: dict,
            pause: float = 0.5) -> tuple[list[Item], list[dict]]:
    """Fetch every request; return (items, per-source health rows)."""
    items, health = [], []
    for r in reqs:
        row = {"source": r["name"], "url": r["url"], "status": "ok", "entries": 0, "on_topic": 0, "error": ""}
        try:
            content = fetch(r["url"])
            got = parse_feed(content, r["name"], r["found_by"], r["query"])
            row["entries"] = len(got)
            if r["filter"] == "keywords":
                got = [i for i in got if is_on_topic(i.text(), keywords, watchlist)
                       or r["found_by"] in ("google_keywords", "google_firm")]
            row["on_topic"] = len(got)
            items.extend(got)
        except Exception as exc:  # one bad source must never stop the run
            row["status"], row["error"] = "error", f"{type(exc).__name__}: {exc}"[:200]
        health.append(row)
        if pause and "news.google.com" in r["url"]:
            time.sleep(pause)
    return items, health
