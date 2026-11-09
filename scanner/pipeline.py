"""Stage 1a pipeline: collect -> dedupe -> resolve -> date-check -> window -> pre-score."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from .collect import Item, build_requests, collect
from .dates import extract
from .dedupe import dedupe
from .matching import prescore


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def resolve_google_urls(items: list[Item], decoder: Optional[Callable] = None,
                        deadline: float = float("inf"), batch: int = 20) -> int:
    """Swap news.google.com redirects for publisher URLs where possible, in
    batches (one request per batch). Best effort: on failure or when time runs
    out, the Google link stays and the feed date is used."""
    todo = [i for i in items if "news.google.com" in i.url]
    if not todo:
        return 0
    if decoder is None:
        try:
            from googlenewsdecoder import gnewsdecoder as decoder
        except ImportError:
            return 0
    resolved = 0
    for start in range(0, len(todo), batch):
        if time.monotonic() > deadline:
            log(f"link decoding stopped at time limit ({start}/{len(todo)} attempted)")
            break
        chunk = todo[start:start + batch]
        try:
            res = decoder([i.url for i in chunk])
        except Exception as exc:
            log(f"link decoding batch failed: {type(exc).__name__}")
            continue
        if isinstance(res, dict):
            res = [res]
        for it, r in zip(chunk, res or []):
            if isinstance(r, dict) and r.get("success") and r.get("decoded_url"):
                it.url = r["decoded_url"]
                resolved += 1
    return resolved


def fetch_pages(items: list[Item], fetch_page: Callable, limit: int, deadline: float,
                workers: int = 12) -> dict:
    """Open article pages in parallel; returns {id(item): html}. Stops
    submitting new work at the deadline."""
    targets = [i for i in items if "news.google.com" not in i.url][:limit]
    out: dict = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(fetch_page, it.url): it for it in targets}
        for fut in as_completed(futs):
            try:
                out[id(futs[fut])] = fut.result()
            except Exception:
                out[id(futs[fut])] = None
            if time.monotonic() > deadline:
                log(f"page fetching stopped at time limit ({len(out)}/{len(targets)} done)")
                for f in futs:
                    f.cancel()
                break
    return out


def run_stage1(cfg: dict, fetch_feed: Callable[[str], bytes], fetch_page: Callable[[str], Optional[str]],
               now: Optional[datetime] = None, window_hours: Optional[float] = None,
               decoder: Optional[Callable] = None, resolve: bool = True) -> dict:
    s = cfg["settings"]
    now = now or datetime.now(timezone.utc)
    window_hours = window_hours or s["window_hours"]
    cutoff = now - timedelta(hours=window_hours)
    when = f"{max(1, int(window_hours // 24) + 1)}d"

    reqs = build_requests(cfg["sources"], cfg["keywords"], cfg["watchlist"], s, when)
    raw, health = collect(reqs, fetch_feed, cfg["keywords"], cfg["watchlist"],
                          pause=cfg.get("_pause", 0.5))
    items = dedupe(raw)

    # Cheap early drop: feed date already well outside the window. Keeps page
    # fetches for plausible candidates. Final decision uses page dates below.
    early_dropped, survivors = [], []
    for it in items:
        if it.rss_date and it.rss_date < cutoff - timedelta(hours=48):
            it.status = "dropped:stale_feed_date"
            it.date, it.date_method = it.rss_date, "rss"
            early_dropped.append(it)
        else:
            survivors.append(it)

    log(f"{len(raw)} entries, {len(items)} after de-duplication, {len(survivors)} worth checking")

    # Check the most promising items first, so a time limit only ever costs
    # the weakest candidates their page-level date check.
    for it in survivors:
        it.prescore, _ = prescore(it.text(), cfg["keywords"], cfg["watchlist"])
    survivors.sort(key=lambda i: i.prescore, reverse=True)
    limit = s.get("max_page_fetches", 150)
    deadline = time.monotonic() + s.get("time_budget_seconds", 600)

    if resolve:
        n = resolve_google_urls(survivors[:limit], decoder,
                                deadline=time.monotonic() + s.get("time_budget_seconds", 600) / 3)
        log(f"decoded {n} Google News links")
        survivors = dedupe(survivors)  # resolution can reveal more duplicates

    pages = {}
    if s.get("fetch_pages_for_dates"):
        pages = fetch_pages(survivors, fetch_page, limit, deadline, s.get("fetch_workers", 12))
        log(f"opened {sum(1 for v in pages.values() if v)} of {len(pages)} pages")
    fetches = len(pages)
    for it in survivors:
        html = pages.get(id(it))
        if html:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            for t in soup(["script", "style", "nav", "header", "footer"]):
                t.decompose()
            it.page_text = soup.get_text(" ", strip=True)[:4000]
        d = extract(it.url, html, it.rss_date, s.get("date_disagreement_hours", 24))
        it.date, it.date_method, it.dates_found = d.chosen, d.method, d.found
        it.date_verified, it.date_note = d.verified, d.note

    kept, unverified, dropped = [], [], list(early_dropped)
    for it in survivors:
        in_window = [dt for dt in it.dates_found.values() if dt >= cutoff]
        if it.date is None:
            it.status = "unverified"
            it.date_note = it.date_note or "no date found"
            unverified.append(it)
        elif it.date_verified:
            if it.date >= cutoff and it.date <= now + timedelta(hours=12):
                it.status = "kept"
                kept.append(it)
            else:
                it.status = "dropped:outside_window"
                dropped.append(it)
        elif in_window:
            it.status = "unverified"
            unverified.append(it)
        else:
            it.status = "dropped:outside_window"
            dropped.append(it)

    for it in kept + unverified:
        it.prescore, it.prescore_reasons = prescore(it.text(), cfg["keywords"], cfg["watchlist"])
    kept.sort(key=lambda i: (i.prescore, i.date), reverse=True)
    unverified.sort(key=lambda i: i.prescore, reverse=True)

    n = s.get("max_candidates_for_vetting", 40)
    return {
        "now": now, "cutoff": cutoff, "window_hours": window_hours,
        "candidates": kept[:n], "overflow": kept[n:], "unverified": unverified[:15],
        "dropped": dropped, "health": health,
        "stats": {"requests": len(reqs), "raw": len(raw), "after_dedupe": len(items),
                  "kept": len(kept), "unverified": len(unverified), "dropped": len(dropped),
                  "page_fetches": fetches},
    }
