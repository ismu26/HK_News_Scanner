"""HK AI and tokenization news scanner.

  python run.py collect            daily run (window from config/settings.yaml)
  python run.py collect --setup    one-off 30-day scan for the labelling set
  python run.py render --vet FILE  build the vetted daily page from vet results
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests
import yaml

from scanner.output import append_log, render_html, write_candidates
from scanner.pipeline import run_stage1

ROOT = Path(__file__).parent


def load_config() -> dict:
    c = ROOT / "config"
    return {name: yaml.safe_load((c / f"{name}.yaml").read_text())
            for name in ("settings", "sources", "keywords", "watchlist")}


def make_fetchers(settings: dict):
    session = requests.Session()
    session.headers.update({
        "User-Agent": settings["user_agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/rss+xml,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
    })
    timeout = settings.get("page_fetch_timeout_seconds", 15)

    def fetch_feed(url: str) -> bytes:
        r = session.get(url, timeout=timeout)
        r.raise_for_status()
        return r.content

    def fetch_page(url: str):
        try:
            r = session.get(url, timeout=timeout)
            if r.ok and "html" in r.headers.get("content-type", ""):
                return r.text
        except requests.RequestException:
            pass
        return None

    return fetch_feed, fetch_page


def cmd_collect(args) -> int:
    cfg = load_config()
    s = cfg["settings"]
    from datetime import datetime
    from zoneinfo import ZoneInfo
    is_monday = datetime.now(ZoneInfo(s["timezone"])).weekday() == 0
    if args.setup:
        window = s["setup_days"] * 24
    elif is_monday:
        window = s.get("monday_window_hours", s["window_hours"])
    else:
        window = s["window_hours"]
    fetch_feed, fetch_page = make_fetchers(s)
    if args.setup:
        s["max_candidates_for_vetting"] = 300
        s["max_page_fetches"] = 400
        s["time_budget_seconds"] = 1200
    result = run_stage1(cfg, fetch_feed, fetch_page, window_hours=window)
    out = ROOT / "data" / ("setup" if args.setup else "runs")
    path = write_candidates(result, out)
    append_log(result, ROOT / "data" / "collection_log.csv")
    payload = json.loads(path.read_text())
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "prescored.html").write_text(render_html(payload))
    print(json.dumps(result["stats"], indent=2))
    bad = [h for h in result["health"] if h["status"] != "ok"]
    print(f"{len(bad)} source(s) failed" + "".join(f"\n  - {h['source']}: {h['error']}" for h in bad))
    return 0


def cmd_render(args) -> int:
    cfg = load_config()
    payload = json.loads(Path(args.candidates).read_text())
    payload["min_vet_score"] = cfg["settings"]["min_vet_score"]
    payload["max_daily_items"] = cfg["settings"]["max_daily_items"]
    vet = json.loads(Path(args.vet).read_text()) if args.vet else None
    out = Path(args.out)
    out.write_text(render_html(payload, vet))
    print(out)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--setup", action="store_true", help="one-off 30-day scan")
    c.set_defaults(func=cmd_collect)
    r = sub.add_parser("render")
    r.add_argument("--candidates", default=str(ROOT / "data" / "runs" / "latest.json"))
    r.add_argument("--vet", help="JSON: {url: {score, reason}}")
    r.add_argument("--out", default=str(ROOT / "docs" / "daily.html"))
    r.set_defaults(func=cmd_render)
    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
