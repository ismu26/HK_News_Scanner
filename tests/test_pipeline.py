"""Offline tests. Run: python -m pytest tests -q"""
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from scanner.dates import extract
from scanner.matching import hits, prescore
from scanner.pipeline import run_stage1

ROOT = Path(__file__).resolve().parents[1]
FX = ROOT / "tests" / "fixtures"


def cfg_for_test():
    c = {n: yaml.safe_load((ROOT / "config" / f"{n}.yaml").read_text())
         for n in ("settings", "sources", "keywords", "watchlist")}
    # One RSS feed, one Google News query, one edition: keeps the test small.
    c["sources"] = {"rss": [{"name": "HKMA press releases", "url": "fixture://hkma", "filter": "keywords"}],
                    "google_news_site_queries": []}
    c["keywords"]["google_news_queries"] = ['"onchain fund"']
    c["settings"]["google_news_editions"] = [{"hl": "en-US", "gl": "US", "ceid": "US:en"}]
    c["_pause"] = 0
    return c


def fetch_feed(url):
    if url == "fixture://hkma":
        return (FX / "hkma.xml").read_bytes()
    if "news.google.com" in url:
        return (FX / "google_news.xml").read_bytes()
    raise RuntimeError("unexpected url " + url)


def fetch_page(url):
    if "jqqq-launch" in url:
        return (FX / "jqqq_page.html").read_text()
    if "archive.test" in url:
        return (FX / "old_page.html").read_text()
    return None


def run_at(now):
    return run_stage1(cfg_for_test(), fetch_feed, fetch_page, now=now, resolve=False)


def titles(items):
    return [i.title for i in items]


def test_jqqq_caught_and_ranked_first_the_morning_after():
    r = run_at(datetime(2026, 10, 29, 0, 0, tzinfo=timezone.utc))  # 08:00 HKT
    top = r["candidates"][0]
    assert top.title.startswith("Janus Henderson and Centrifuge")
    assert top.date_method == "jsonld"               # original date, not modified
    assert top.date == datetime(2026, 10, 28, 1, 0, tzinfo=timezone.utc)
    assert top.prescore >= 6


def test_tokenised_duplicate_merged():
    r = run_at(datetime(2026, 10, 29, 0, 0, tzinfo=timezone.utc))
    jqqq = [t for t in titles(r["candidates"] + r["unverified"]) if "JQQQ" in t]
    assert len(jqqq) == 1


def test_resurfaced_old_story_not_in_main_list():
    r = run_at(datetime(2026, 10, 29, 0, 0, tzinfo=timezone.utc))
    assert not any("Old story" in t for t in titles(r["candidates"]))
    old = [i for i in r["unverified"] + r["dropped"] if "Old story" in i.title]
    assert old and old[0].status in ("unverified", "dropped:outside_window")
    assert "2025-06-10" in str(old[0].dates_found.get("meta"))


def test_noise_ranked_below_real_news():
    r = run_at(datetime(2026, 10, 29, 0, 0, tzinfo=timezone.utc))
    order = titles(r["candidates"])
    btc = next(i for i, t in enumerate(order) if t.startswith("Bitcoin price"))
    jqqq = next(i for i, t in enumerate(order) if "JQQQ" in t)
    assert jqqq < btc


def test_off_topic_regulator_item_filtered():
    r = run_at(datetime(2026, 10, 29, 0, 0, tzinfo=timezone.utc))
    assert not any("Exchange Fund Bills" in t for t in titles(r["candidates"]))


def test_jqqq_outside_window_today():
    r = run_at(datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc))
    assert not any("JQQQ" in t for t in titles(r["candidates"]))


def test_setup_window_30_days_keeps_jqqq():
    c = cfg_for_test()
    r = run_stage1(c, fetch_feed, fetch_page, now=datetime(2026, 11, 9, tzinfo=timezone.utc),
                   window_hours=30 * 24, resolve=False)
    assert any("JQQQ" in t for t in titles(r["candidates"]))


@pytest.mark.parametrize("text", ["tokenised fund", "Tokenized Funds", "TOKENIZATION", "on-chain fund", "onchain funds"])
def test_spelling_variants(text):
    groups = ["tokeniz", "tokenized fund", "onchain fund"]
    assert hits(text, groups)


def test_acronyms_case_sensitive():
    assert not hits("Merry Christmas", ["MAS"])
    assert hits("MAS said today", ["MAS"])


def test_date_disagreement_flagged():
    page = '<html><head><meta property="article:published_time" content="2026-10-01T00:00:00Z"></head></html>'
    d = extract("https://x.test/a", page, datetime(2026, 10, 28, tzinfo=timezone.utc))
    assert d.method == "meta" and not d.verified


def test_modified_date_ignored():
    page = '<html><head><meta property="article:modified_time" content="2026-11-05T00:00:00Z"></head></html>'
    d = extract("https://x.test/2026/10/28/a", page, None)
    assert d.method == "url" and d.chosen.date().isoformat() == "2026-10-28"


def test_press_release_alone_without_firm_on_watchlist():
    """Lesson from JQQQ: Janus Henderson and Centrifuge were not on the original
    watchlist. Remove them and check the story is still picked up and ranked
    above noise, via page text (tokenized funds, Stellar) rather than headline."""
    c = cfg_for_test()
    c["watchlist"]["firms"] = [f for f in c["watchlist"]["firms"]
                               if f["name"] not in ("Janus Henderson", "Centrifuge")]
    r = run_stage1(c, fetch_feed, fetch_page, now=datetime(2026, 10, 29, tzinfo=timezone.utc), resolve=False)
    jqqq = next(i for i in r["candidates"] if "JQQQ" in i.title)
    btc = next(i for i in r["candidates"] if i.title.startswith("Bitcoin price"))
    assert jqqq.prescore > btc.prescore


def test_also_covered_by_listed_once():
    r = run_at(datetime(2026, 10, 29, 0, 0, tzinfo=timezone.utc))
    jqqq = next(i for i in r["candidates"] if "JQQQ" in i.title)
    assert [a[0] for a in jqqq.also] == ["Ledger Test"]


def test_page_fetching_respects_time_limit():
    """The set-up run timed out on GitHub (9 Nov). Slow pages must not be able
    to hold the run past its time budget."""
    import time
    from scanner.collect import Item
    from scanner.pipeline import fetch_pages

    def slow(url):
        time.sleep(0.3)
        return "<html></html>"

    items = [Item(title=f"t{i}", url=f"https://x.test/{i}", source="x", found_by={"rss"}) for i in range(100)]
    start = time.monotonic()
    got = fetch_pages(items, slow, limit=100, deadline=start + 0.5, workers=4)
    assert time.monotonic() - start < 2.0
    assert 0 < len(got) < 100


REAL_HEADLINES = [  # from the 9 Nov set-up run
    "Janus Henderson And Centrifuge Introduce Licensed Blockchain Native Nasdaq-100 Fund",
    "Janus Henderson brings Nasdaq-100 onchain with tokenized fund JQQQ",
    "Nasdaq-100 comes onchain as Janus Henderson and Centrifuge launch first licensed fund",
    "Here's Why Janus Henderson is Bullish On Meta Platforms, Inc. (META)",
    "Mirae Asset Securities Issues Tokenised Funding Notes on Solana With Marketnode",
    "Mirae Asset Securities HK issues 1st dollar-denominated digital bond using proprietary technology",
]


def test_story_grouping_on_real_headlines():
    from scanner.collect import Item
    from scanner.dedupe import dedupe
    d = datetime(2026, 10, 28, tzinfo=timezone.utc)
    items = [Item(title=t, url=f"https://x.test/{i}", source="x", found_by={"rss"}, rss_date=d)
             for i, t in enumerate(REAL_HEADLINES)]
    out = dedupe(items)
    titles_out = [i.title for i in out]
    assert len(out) == 4                                          # 3 JQQQ headlines -> 1 story
    assert any("Meta Platforms" in t for t in titles_out)         # unrelated Janus news stays separate
    assert any("Marketnode" in t for t in titles_out)             # Marketnode story not swallowed
    assert any("digital bond" in t for t in titles_out)
