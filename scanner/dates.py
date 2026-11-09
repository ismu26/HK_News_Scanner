"""Publication-date extraction.

Order of preference (original publication date, never "modified"):
  1. JSON-LD "datePublished"
  2. Meta tags (article:published_time, pubdate, date, ...)
  3. RSS / news-feed entry date
  4. A date in the URL (/2026/11/09/ or 20261109)
  5. The visible dateline or byline on the page
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from bs4 import BeautifulSoup
from dateutil import parser as dtparser

PRIORITY = ["jsonld", "meta", "rss", "url", "visible"]

META_KEYS = [
    "article:published_time",
    "og:published_time",
    "published_time",
    "pubdate",
    "publishdate",
    "publish_date",
    "dc.date.issued",
    "dc.date",
    "date",
    "sailthru.date",
    "parsely-pub-date",
]
# Keys that look like dates but describe edits, not publication. Never used.
IGNORED_META = {"article:modified_time", "og:updated_time", "last-modified", "dc.date.modified"}

MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
VISIBLE_PATTERNS = [
    re.compile(rf"\b(\d{{1,2}}\s+(?:{MONTHS})[a-z]*\.?\s+\d{{4}})\b", re.I),
    re.compile(rf"\b((?:{MONTHS})[a-z]*\.?\s+\d{{1,2}},?\s+\d{{4}})\b", re.I),
]
URL_PATTERNS = [
    re.compile(r"/(20\d{2})/(\d{1,2})/(\d{1,2})(?:/|$|-)"),
    re.compile(r"/(20\d{2})-(\d{2})-(\d{2})"),
    re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)"),
]


@dataclass
class DateResult:
    chosen: Optional[datetime]
    method: str  # which source was chosen, e.g. "jsonld"
    found: dict = field(default_factory=dict)  # method -> datetime
    verified: bool = True
    note: str = ""


def _to_utc(dt: datetime, default_tz=timezone.utc) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=default_tz)
    return dt.astimezone(timezone.utc)


def parse_any(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return _to_utc(value)
    try:
        return _to_utc(dtparser.parse(str(value), fuzzy=False))
    except (ValueError, OverflowError, TypeError):
        return None


def _walk_jsonld(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk_jsonld(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_jsonld(v)


def from_jsonld(soup: BeautifulSoup) -> Optional[datetime]:
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _walk_jsonld(data):
            if "datePublished" in node:
                dt = parse_any(node["datePublished"])
                if dt:
                    return dt
    return None


def from_meta(soup: BeautifulSoup) -> Optional[datetime]:
    metas = {}
    for m in soup.find_all("meta"):
        key = (m.get("property") or m.get("name") or m.get("itemprop") or "").strip().lower()
        if key and key not in IGNORED_META and m.get("content"):
            metas.setdefault(key, m["content"])
    for key in META_KEYS:
        if key in metas:
            dt = parse_any(metas[key])
            if dt:
                return dt
    for t in soup.find_all("time"):
        if t.get("datetime") and "updated" not in " ".join(t.get("class", [])).lower():
            dt = parse_any(t["datetime"])
            if dt:
                return dt
    return None


def from_url(url: str) -> Optional[datetime]:
    for pat in URL_PATTERNS:
        m = pat.search(url or "")
        if m:
            y, mo, d = (int(x) for x in m.groups())
            try:
                return datetime(y, mo, d, tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def from_visible(soup: BeautifulSoup) -> Optional[datetime]:
    text = soup.get_text(" ", strip=True)[:4000]  # datelines sit near the top
    for pat in VISIBLE_PATTERNS:
        m = pat.search(text)
        if m:
            dt = parse_any(m.group(1))
            if dt:
                return dt
    return None


def extract(url: str, html: Optional[str], rss_date: Optional[datetime],
            disagreement_hours: float = 24) -> DateResult:
    found: dict = {}
    if html:
        soup = BeautifulSoup(html, "html.parser")
        for name, fn in (("jsonld", from_jsonld), ("meta", from_meta), ("visible", from_visible)):
            dt = fn(soup)
            if dt:
                found[name] = dt
    if rss_date:
        found["rss"] = _to_utc(rss_date)
    u = from_url(url)
    if u:
        found["url"] = u

    if not found:
        return DateResult(None, "none", found, verified=False, note="no date found")

    method = next(m for m in PRIORITY if m in found)
    chosen = found[method]

    # Compare precise sources against each other. URL and visible dates are
    # day-precision only, so allow a full extra day of slack for them.
    verified, note = True, ""
    for m, dt in found.items():
        if m == method:
            continue
        slack = disagreement_hours + (24 if m in ("url", "visible") or method in ("url", "visible") else 0)
        if abs((dt - chosen).total_seconds()) > slack * 3600:
            verified = False
            note = f"{method}={chosen.date()} vs {m}={dt.date()}"
            break
    return DateResult(chosen, method, found, verified, note)
