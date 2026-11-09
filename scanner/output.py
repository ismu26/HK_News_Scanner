"""Write candidates JSON, the collection log CSV, and the daily HTML page."""
from __future__ import annotations

import csv
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .collect import Item

HKT = ZoneInfo("Asia/Hong_Kong")
METHOD_LABEL = {"jsonld": "page structured data", "meta": "page meta tag", "rss": "feed date",
                "url": "date in URL", "visible": "visible dateline", "none": "not found"}

LOG_FIELDS = ["run_date", "title", "url", "source", "found_by", "published", "date_method",
              "age_hours", "status", "prescore"]


def item_to_dict(it: Item, now: datetime) -> dict:
    age = round((now - it.date).total_seconds() / 3600, 1) if it.date else None
    return {
        "title": it.title, "url": it.url, "google_url": it.google_url, "source": it.source,
        "found_by": sorted(it.found_by), "queries": it.queries,
        "published": it.date.isoformat() if it.date else None,
        "date_method": it.date_method,
        "dates_found": {k: v.isoformat() for k, v in it.dates_found.items()},
        "date_verified": it.date_verified, "date_note": it.date_note,
        "age_hours": age, "snippet": it.snippet, "prescore": it.prescore,
        "prescore_reasons": it.prescore_reasons, "status": it.status,
        "also_covered_by": [{"source": a, "url": b, "title": t} for a, b, t in it.also],
    }


def write_candidates(result: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    now = result["now"]
    payload = {
        "generated_at": now.isoformat(),
        "window_hours": result["window_hours"],
        "window_start": result["cutoff"].isoformat(),
        "stats": result["stats"],
        "candidates": [item_to_dict(i, now) for i in result["candidates"]],
        "unverified": [item_to_dict(i, now) for i in result["unverified"]],
        "source_health": result["health"],
    }
    day = now.astimezone(HKT).strftime("%Y-%m-%d")
    path = out_dir / f"candidates-{day}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    (out_dir / "latest.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return path


def append_log(result: dict, log_path: Path) -> None:
    """Every story seen, with its age, so stale-prone sources show up over time."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    new = not log_path.exists()
    now = result["now"]
    run_date = now.astimezone(HKT).strftime("%Y-%m-%d")
    rows = result["candidates"] + result["overflow"] + result["unverified"] + result["dropped"]
    with log_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        for it in rows:
            age = round((now - it.date).total_seconds() / 3600, 1) if it.date else ""
            w.writerow({"run_date": run_date, "title": it.title, "url": it.url, "source": it.source,
                        "found_by": "+".join(sorted(it.found_by)),
                        "published": it.date.isoformat() if it.date else "",
                        "date_method": it.date_method, "age_hours": age,
                        "status": it.status, "prescore": it.prescore})


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "date unknown"
    return datetime.fromisoformat(iso).astimezone(HKT).strftime("%d %b %Y, %H:%M HKT")


def _found_by_label(fb: list) -> str:
    kw = any(x.startswith("google") or x == "rss" for x in fb)
    ai = "ai_search" in fb
    return "both" if kw and ai else ("AI search" if ai else "keywords")


def render_html(payload: dict, vet: dict | None = None) -> str:
    """Daily list. With `vet` (url -> {score, reason}) it shows the vetted top
    list; without it, the free pre-scored backstop list."""
    e = html.escape
    gen = _fmt_date(payload["generated_at"])
    if vet:
        rows = []
        for c in payload["candidates"] + payload.get("ai_search", []):
            v = vet.get(c["url"])
            if v and v.get("score", 0) >= payload.get("min_vet_score", 3):
                rows.append((v["score"], c, v.get("reason", "")))
        rows.sort(key=lambda r: (r[0], r[1].get("published") or ""), reverse=True)
        rows = rows[: payload.get("max_daily_items", 10)]
        heading, mode = "Today's shortlist", "AI-vetted against your criteria"
    else:
        rows = [(None, c, "; ".join(c["prescore_reasons"])) for c in payload["candidates"][:10]]
        heading, mode = "Today's shortlist", "Keyword-ranked (AI vetting not run)"

    def card(score, c, reason):
        badge = f'<span class="score s{score}">{score}/5</span>' if score is not None \
            else f'<span class="score pre">pre {c["prescore"]}</span>'
        return f"""
      <li class="item">
        <div class="top">{badge}<a href="{e(c['url'])}" target="_blank" rel="noopener">{e(c['title'])}</a></div>
        <div class="meta">{e(c['source'])} · {e(_fmt_date(c.get('published')))}
          <span class="how">({e(METHOD_LABEL.get(c.get('date_method') or 'none', c.get('date_method') or ''))})</span>
          · found by {e(_found_by_label(c['found_by']))}</div>
        <div class="reason">{e(reason)}</div>
      </li>"""

    items_html = "".join(card(*r) for r in rows) or '<li class="empty">Nothing cleared the bar today.</li>'
    unv = payload.get("unverified", [])
    unv_html = "".join(f"""
      <li class="item small"><a href="{e(c['url'])}" target="_blank" rel="noopener">{e(c['title'])}</a>
        <div class="meta">{e(c['source'])} · dates found: {e(', '.join(f'{k} {v[:10]}' for k, v in c['dates_found'].items()) or 'none')}
        {(' · ' + e(c['date_note'])) if c.get('date_note') else ''}</div></li>""" for c in unv)
    bad = [h for h in payload.get("source_health", []) if h["status"] != "ok"]
    health_html = "".join(f"<li>{e(h['source'])}: {e(h['error'])}</li>" for h in bad) or "<li>All sources responded.</li>"
    st = payload["stats"]

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>HK News Scanner</title>
<style>
:root {{ --bg:#f7f6f3; --card:#fff; --ink:#1d1d1b; --muted:#6b6a66; --line:#e4e2dc; --accent:#0b5d55;
        --s5:#0b5d55; --s4:#2f7f6f; --s3:#8a7a2c; --pre:#77746c; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#151514; --card:#1f1f1d; --ink:#ecebe7; --muted:#a3a19b; --line:#33322f; --accent:#5cc2b2; --s5:#5cc2b2; --s4:#8fd0c3; --s3:#d6c36b; --pre:#a3a19b; }} }}
:root[data-theme="dark"] {{ --bg:#151514; --card:#1f1f1d; --ink:#ecebe7; --muted:#a3a19b; --line:#33322f; --accent:#5cc2b2; --s5:#5cc2b2; --s4:#8fd0c3; --s3:#d6c36b; --pre:#a3a19b; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif; }}
main {{ max-width:760px; margin:0 auto; padding:28px 16px 48px; }}
h1 {{ font-size:22px; margin:0 0 4px; }} h2 {{ font-size:16px; margin:32px 0 8px; color:var(--muted); font-weight:600; }}
.sub {{ color:var(--muted); font-size:13px; }}
ul {{ list-style:none; padding:0; margin:0; }}
.item {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; margin:10px 0; }}
.item.small {{ padding:9px 12px; font-size:14px; }}
.top {{ display:flex; gap:10px; align-items:baseline; }}
.top a {{ color:var(--ink); font-weight:600; text-decoration:none; }} .top a:hover {{ text-decoration:underline; }}
.small a {{ color:var(--ink); }}
.score {{ flex:none; font-size:12px; font-weight:700; padding:1px 7px; border-radius:20px; border:1px solid currentColor; }}
.s5 {{ color:var(--s5); }} .s4 {{ color:var(--s4); }} .s3 {{ color:var(--s3); }} .pre {{ color:var(--pre); }}
.meta {{ color:var(--muted); font-size:13px; margin-top:4px; }} .how {{ font-style:italic; }}
.reason {{ margin-top:6px; font-size:14px; }}
.empty {{ color:var(--muted); }}
footer {{ margin-top:36px; color:var(--muted); font-size:12px; }}
footer li {{ margin:2px 0; }}
</style></head>
<body><main>
  <h1>{heading}</h1>
  <div class="sub">{e(mode)} · generated {e(gen)} · window {payload['window_hours']}h</div>
  <ul>{items_html}</ul>
  <h2>Date unverified</h2>
  <div class="sub">Sources disagree on the date, or none was found. Check before acting.</div>
  <ul>{unv_html or '<li class="empty">None today.</li>'}</ul>
  <footer>
    <p>{st['requests']} feed requests · {st['raw']} entries · {st['after_dedupe']} after de-duplication ·
       {st['kept']} in window · {st['dropped']} outside window · {st['page_fetches']} pages opened to check dates</p>
    <ul>{health_html}</ul>
  </footer>
</main></body></html>"""
