"""Free keyword pre-score (no AI)."""
from __future__ import annotations

import re
from functools import lru_cache

# "tokeniz" / "tokenis" stems expand to every spelling and word form.
_TOKEN_STEM = re.compile(r"tokeni[sz]", re.I)


def _term_regex(term: str) -> str:
    t = term.strip()
    # make spaces/hyphens interchangeable: "on-chain" ~ "on chain" ~ "onchain"
    parts = re.split(r"[\s\-]+", t)
    esc = r"[\s\-]?".join(re.escape(p) for p in parts)
    # "onchain" / "offchain" are also written "on-chain" / "on chain"
    esc = re.sub(r"(?i)\b(on|off)chain", r"\1[\\s\\-]?chain", esc)
    if _TOKEN_STEM.search(t):
        esc = re.sub(r"tokeni[sz]", "tokeni[sz]", esc, flags=re.I)
        # "tokeniz" alone should match tokenized / tokenisation / tokenise ...
        esc = re.sub(r"(tokeni\[sz\])(?![a-z])", r"\1[a-z]*", esc, flags=re.I)
        # "tokenized fund" should also match "tokenised funds"
    if not esc.endswith("[a-z]*"):
        esc += r"s?"
    return rf"(?<![A-Za-z0-9]){esc}(?![A-Za-z0-9])"


@lru_cache(maxsize=None)
def compile_terms(terms: tuple, case_sensitive_short: bool = True):
    pats = []
    for t in terms:
        # Short all-caps acronyms (RWA, MAS, SFC) are matched case-sensitively
        # so "mas" in "Christmas" or "rwa" never count.
        flags = 0 if (case_sensitive_short and t.isupper() and len(t) <= 5) else re.I
        pats.append((t, re.compile(_term_regex(t), flags)))
    return pats


def hits(text: str, terms) -> list:
    return [t for t, p in compile_terms(tuple(terms)) if p.search(text or "")]


def mentions_tokenization(text: str) -> bool:
    return bool(re.search(r"tokeni[sz]", text or "", re.I))


def prescore(text: str, keywords: dict, watchlist: dict) -> tuple[int, list]:
    """Return (score, reasons). Higher is better. Used only to rank and trim
    candidates before AI vetting, and as the backstop list if vetting fails."""
    score, reasons = 0, []
    for name, group in keywords.get("groups", {}).items():
        h = hits(text, group["terms"])
        if h:
            score += group["weight"]
            reasons.append(f"{name}: {', '.join(h[:3])}")
    tok = mentions_tokenization(text)
    firm_hits = []
    for f in watchlist.get("firms", []):
        names = [f["name"]] + f.get("aliases", [])
        if hits(text, names) and (tok or not f.get("require_tokenization")):
            firm_hits.append(f["name"])
    # "Stellar" inside "Stellar Development Foundation" is one firm, not two.
    firm_hits = [f for f in firm_hits if not any(f != g and f in g for g in firm_hits)]
    if firm_hits:
        score += watchlist.get("weight", 3) + (len(firm_hits) - 1)
        reasons.append(f"watched firms: {', '.join(firm_hits)}")
    for key in ("geography", "noise", "forecast"):
        block = keywords.get(key)
        if block:
            h = hits(text, block["terms"])
            if h:
                score += block["weight"]
                reasons.append(f"{key}: {', '.join(h[:3])}")
    return score, reasons


def is_on_topic(text: str, keywords: dict, watchlist: dict) -> bool:
    """Gate for broad feeds: needs at least one topic keyword or watched firm."""
    for group in keywords.get("groups", {}).values():
        if hits(text, group["terms"]):
            return True
    tok = mentions_tokenization(text)
    for f in watchlist.get("firms", []):
        names = [f["name"]] + f.get("aliases", [])
        if hits(text, names) and (tok or not f.get("require_tokenization")):
            return True
    return False
