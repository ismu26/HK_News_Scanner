"""Remove duplicates and group coverage of the same story.

Two items are the same story if they share a URL, have near-identical
headlines, or (within 4 days of each other) share at least three distinctive
headline words. Each new headline is compared with the story's lead headline
only, which stops chains of loosely related stories merging.

The earliest-published item becomes the main link (usually the company's own
press release); the rest are listed under "also covered by"."""
from __future__ import annotations

import re

from .collect import Item, canonical_url

STOPWORDS = {"a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "by", "at",
             "as", "its", "is", "new", "says", "say"}


def norm_words(t: str) -> set:
    t = re.sub(r"tokenis", "tokeniz", t.lower())
    words = re.findall(r"[a-z0-9]+", t)
    out = set()
    for w in words:
        if w in STOPWORDS:
            continue
        # crude plural/verb folding: launches/launch, funds/fund
        for suf in ("es", "s"):
            if len(w) > 4 and w.endswith(suf):
                w = w[: -len(suf)]
                break
        out.add(w)
    return out


# Words too common in this beat to show two headlines are the same story.
GENERIC = {"fund", "launch", "launche", "tokenized", "tokenize", "tokenization", "tokeniz",
           "first", "blockchain", "onchain", "bring", "digital", "asset", "hong", "kong", "bank",
           "issue", "issu", "solana", "stablecoin", "deposit", "settlement", "hkma", "via"}


def similar(a: set, b: set, threshold: float = 0.75, da=None, db=None) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    inter = a & b
    if min(len(a), len(b)) >= 5 and len(inter) / len(a | b) >= threshold:
        return True
    if da and db and abs((da - db).total_seconds()) > 4 * 86400:
        return False
    return len(inter - GENERIC) >= 3 and len(inter) / min(len(a), len(b)) >= 0.5


def _merge(keep: Item, other: Item) -> None:
    keep.found_by |= other.found_by
    keep.queries = sorted(set(keep.queries) | set(other.queries))
    other_first = other.rss_date and (not keep.rss_date or other.rss_date < keep.rss_date)
    keep_is_google = "news.google.com" in keep.url and "news.google.com" not in other.url
    # Keep both headlines and snippets for scoring, whichever becomes the main link.
    keep.extra_text = "\n".join(x for x in (keep.extra_text, keep.title, keep.snippet,
                                             other.title, other.snippet, other.extra_text) if x)
    if other_first or keep_is_google:
        keep.also.append((keep.source, keep.url, keep.title))
        keep.title, keep.url, keep.source = other.title, other.url, other.source
        if other.rss_date:
            keep.rss_date = other.rss_date
        if other.google_url:
            keep.google_url = other.google_url
    elif canonical_url(other.url) != canonical_url(keep.url):
        keep.also.append((other.source, other.url, other.title))
    if len(other.snippet) > len(keep.snippet):
        keep.snippet = other.snippet


def _tidy_also(it: Item) -> None:
    main, seen, out = canonical_url(it.url), set(), []
    for src, url, title in it.also:
        k = canonical_url(url)
        if k != main and k not in seen:
            seen.add(k)
            out.append((src, url, title))
    it.also = out


def dedupe(items: list[Item], threshold: float = 0.75) -> list[Item]:
    out: list[Item] = []
    leads: list[tuple] = []   # (lead headline words, lead date) per story
    by_url: dict = {}
    for it in items:
        key = canonical_url(it.url)
        if key in by_url:
            _merge(by_url[key], it)
            continue
        words = norm_words(it.title)
        match = None
        for idx, (lw, ld) in enumerate(leads):
            if similar(lw, words, threshold, ld, it.rss_date):
                match = out[idx]
                break
        if match:
            _merge(match, it)
            by_url[key] = match
            continue
        by_url[key] = it
        out.append(it)
        leads.append((words, it.rss_date))
    for it in out:
        _tidy_also(it)
    return out
