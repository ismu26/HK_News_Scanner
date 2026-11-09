# Stage 2: vetting instructions

These are the standing instructions for the 7:30 HKT vetting session (a Claude scheduled task on
Ishaan's plan). The scheduled task's prompt says: "Follow prompts/vet.md in the hk-news-scanner repo."

## Inputs

1. `data/runs/latest.json` from the repo: up to 40 pre-scored candidates from the 6:45 HKT
   collection run, plus a "date unverified" list. Check `generated_at` is from today (HKT). If it
   is older, say so at the top of the page and vet what is there.
2. `config/criteria.md`: the current relevance criteria and 0 to 5 scale. Follow it exactly.
3. Labelled examples: rows in the Google Sheet "Labels" tab marked use_as_example = yes (fall
   back to `config/labels.csv` if the Sheet is unreachable). Use at most 15, mixing relevant and
   not relevant, most recent first.

## How to score each candidate

- Each candidate is a story: the main link plus other outlets' headlines in also_covered_by.
  If those headlines describe a different event (the grouping is automatic and can be wrong),
  say so in the reason. If two candidates are the same event, score the better source and
  give the other 0 with reason "duplicate of <title>".
- Prefer the original source (company or regulator release) as the main link when it is
  among the grouped links.
- Judge from the title, source, date, snippet and also_covered_by. Do not open the page unless
  the snippet is empty or the title is ambiguous; in that case open it once.
- Give a score of 0 to 5 per the scale, and a one-line reason (under 25 words) naming the concrete
  fact that earned the score, or why it fell short. Example of a good reason:
  "5: Janus Henderson launches tokenized Nasdaq-100 fund on Stellar (Marketnode investor), HK dateline."
- Treat survey findings as the middle tier and say "survey data" in the reason.
- Forecast-only, price, meme-coin, marketing and repackaged-old-news items score 0 to 1.
- Never invent facts. If the snippet is too thin to judge, score on what is there and say
  "thin snippet" in the reason.
- Do not summarise articles. Reasons only.

## Output

1. Write `vet.json` as `{"<url>": {"score": n, "reason": "..."}}` for every candidate.
2. Run `python run.py render --vet vet.json --out daily.html` and publish `daily.html` as the
   daily artifact (same artifact every day, so the link stays the same).
3. Append today's items scoring 3+ (max 10) to the Google Sheet "Daily" tab with columns:
   date, title, source, published, date_method, url, score, reason, found_by, my_label, my_reason.
   Leave my_label and my_reason blank for Ishaan.
4. If vetting cannot finish, publish `docs/prescored.html` (the free keyword-ranked list) instead
   and say so at the top. The day should never end with no list.

## Never

- Put confidential Marketnode information anywhere in this pipeline.
- Change criteria, keywords or the watchlist. Proposals go in the weekly review for Ishaan to approve.
