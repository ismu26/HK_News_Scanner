"""Remove duplicates: same URL, or near-identical headline.

When two items are the same story, the earliest-published one is kept as the
main link (usually the company's own press release) and the others are
listed under "also covered by"."""
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


def similar(a: set, b: set, threshold: float) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    small = min(len(a), len(b))
    if small < 5:  # very short headlines: require an exact match
        return False
    return len(a & b) / len(a | b) >= threshold


def _merge(keep: Item, other: Item) -> None:
    keep.found_by |= other.found_by
    keep.queries = sorted(set(keep.queries) | set(other.queries))
    other_first = other.rss_date and (not keep.rss_date or other.rss_date < keep.rss_date)
    keep_is_google = "news.google.com" in keep.url and "news.google.com" not in other.url
    # Keep both headlines and snippets for scoring, whichever becomes the main link.
    keep.extra_text = "\n".join(x for x in (keep.extra_text, keep.title, keep.snippet,
                                             other.title, other.snippet, other.extra_text) if x)
    if other_first or keep_is_google:
        keep.also.append((keep.source, keep.url))
        keep.title, keep.url, keep.source = other.title, other.url, other.source
        if other.rss_date:
            keep.rss_date = other.rss_date
        if other.google_url:
            keep.google_url = other.google_url
    elif canonical_url(other.url) != canonical_url(keep.url):
        keep.also.append((other.source, other.url))
    if len(other.snippet) > len(keep.snippet):
        keep.snippet = other.snippet


def _tidy_also(it: Item) -> None:
    main, seen, out = canonical_url(it.url), set(), []
    for src, url in it.also:
        k = canonical_url(url)
        if k != main and k not in seen:
            seen.add(k)
            out.append((src, url))
    it.also = out


def dedupe(items: list[Item], threshold: float = 0.75) -> list[Item]:
    out: list[Item] = []
    by_url: dict = {}
    for it in items:
        key = canonical_url(it.url)
        if key in by_url:
            _merge(by_url[key], it)
            continue
        words = norm_words(it.title)
        match = next((ex for ex in out if similar(norm_words(ex.title), words, threshold)), None)
        if match:
            _merge(match, it)
            by_url[key] = match
            continue
        by_url[key] = it
        out.append(it)
    for it in out:
        _tidy_also(it)
    return out
