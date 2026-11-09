# HK AI and Tokenization News Scanner

A daily sweep of Hong Kong / APAC AI and tokenization news that hands back a short list of links
worth reading. No summaries. Runs at no extra cost: free GitHub Actions for collection, and a Claude
scheduled task on an existing Pro plan for vetting.

**Do not put confidential Marketnode information anywhere in this repo or its Google Sheet.**

## How a day runs

| Time (HKT) | Step | Where | AI? |
|---|---|---|---|
| 06:45 | Collect RSS feeds + Google News searches, de-duplicate, check publication dates, drop anything outside the window, keyword pre-score, keep top 40 | GitHub Actions (`.github/workflows/collect.yml`) | No |
| 07:30 | Score the 40 against `config/criteria.md` + labelled examples, keep 3+ (max 10), publish the daily page, append to the Google Sheet | Claude scheduled task, following `prompts/vet.md` | Yes |
| any time | Mark each item relevant / not relevant in the Sheet | You | |

If vetting fails, the keyword-ranked list in `docs/prescored.html` is the backstop.

## What to edit

| File | What it controls |
|---|---|
| `config/settings.yaml` | Date window (36h; 84h on Mondays), set-up scan length (30 days), caps |
| `config/sources.yaml` | RSS feeds and Google News `site:` searches. `verified: false` = URL not yet confirmed |
| `config/keywords.yaml` | Keyword groups and weights, noise and forecast markers, Google News queries |
| `config/watchlist.yaml` | Watched firms |
| `config/criteria.md` | Relevance definition and 0 to 5 scale used by the vetting step |
| `config/labels.csv` | Seed labelled examples (the Google Sheet takes over once running) |

Spelling: writing `tokeniz` in a keyword matches tokenized/tokenised/tokenization/tokenisation.

## Dates

Each candidate's page is opened to find its original publication date, in this order: JSON-LD
`datePublished` → meta tags (`article:published_time` etc.) → feed date → date in URL → visible
dateline. "Modified" / "updated" dates are ignored. If two sources disagree by more than a day, or
none is found, the story goes to a separate "Date unverified" section with the dates shown. Every
story seen, with its age, is appended to `data/collection_log.csv`, so stale-prone sources show up.

## Commands

```
pip install -r requirements.txt
python -m pytest tests -q          # offline tests, incl. the JQQQ example
python run.py collect              # daily run
python run.py collect --setup      # one-off 30-day scan for labelling
python run.py render --vet vet.json --out daily.html
```

## Status (9 Nov 2026)

Built and tested offline: collector, date checks, de-duplication, pre-score, page rendering,
vetting instructions. Not yet done: first live run on GitHub, the scheduled vetting task, the
Google Sheet, AI search (Stage 1b), long-document mode, weekly feedback review, events mode.
