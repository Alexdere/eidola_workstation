"""The voice: one thing you talk to.

ITERATION 1d   new in this iteration

    hermione ask "remind me what I added this week"
    hermione ask "what have I been into lately"
    hermione ask "what am I missing"
    hermione ask "what's on the black drive"

Design stance, and it matters more than the implementation:

**No model.** This is a deterministic intent router -- keyword patterns over
the corpus journal, the Ledger, and the Cortex. It is not trying to be a
chatbot and it should never guess.

The reason is that the bag's whole claim on your trust is that it does not
make things up. A stochastic layer sitting on top of an honest index would
inherit none of that honesty and spend all of it. So: if a question doesn't
match a known shape, Hermione says so plainly and searches instead. Falling
back to search is always a defensible answer.

An LLM slots in later as a *better parser* -- turning phrasing into one of
these intents -- and never as the answerer. The answers stay computed. That
boundary is the point, and it should survive every future iteration.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable, List, Optional


@dataclass
class Answer:
    text: str
    intent: str
    detail: Optional[dict] = None


# --- intent registry -----------------------------------------------------
_INTENTS: List[tuple] = []


def intent(name: str, *patterns: str):
    """Register a handler. Patterns are matched case-insensitively."""
    compiled = [re.compile(p, re.I) for p in patterns]

    def deco(fn: Callable):
        _INTENTS.append((name, compiled, fn))
        return fn
    return deco


def _window(text: str, default: str = "week") -> str:
    """Pull a time window out of the phrasing."""
    t = text.lower()
    if re.search(r"\btoday\b|\bso far today\b", t):
        return "today"
    if re.search(r"\byesterday\b", t):
        return "yesterday"
    if re.search(r"\bthis week\b|\blast week\b|\bthe week\b|\b7 days\b", t):
        return "week"
    if re.search(r"\bfortnight\b|\btwo weeks\b|\b14 days\b", t):
        return "fortnight"
    if re.search(r"\bthis month\b|\blast month\b|\b30 days\b", t):
        return "month"
    if re.search(r"\bquarter\b|\b3 months\b", t):
        return "quarter"
    if re.search(r"\bthis year\b|\blast year\b", t):
        return "year"
    if re.search(r"\bever\b|\ball time\b|\balways\b|\bin total\b", t):
        return "ever"
    return default


_IRREGULAR = {"search": "searches", "thing": "things", "vault": "vaults",
              "revision": "revisions", "version": "versions"}


def _plural(n: int, one: str, many: Optional[str] = None) -> str:
    """Count plus correctly-inflected noun.

    Small thing, but this is the surface that is supposed to feel like one
    thing you talk to. "10 searchs" undoes that in a single word.
    """
    if n == 1:
        return f"{n} {one}"
    return f"{n} {many or _IRREGULAR.get(one, one + 's')}"


def _phrase(window: str) -> str:
    return {"today": "today", "yesterday": "since yesterday",
            "week": "this week", "fortnight": "in the last fortnight",
            "month": "this month", "quarter": "this quarter",
            "year": "this year", "ever": "in total"}.get(window, window)


# --- intents -------------------------------------------------------------

@intent("additions",
        r"\bwhat.*(did|have) i (add|put|drop|save)",
        r"\bremind me what i (add|put|drop|save)",
        r"\bwhat'?s? new\b", r"\bwhat did i add\b", r"\brecent additions\b",
        r"\banything new\b")
def _additions(bag, q: str) -> Answer:
    window = _window(q)
    s = bag.corpus.summarise(window)
    t = s["totals"]
    added = t.get("ingested", 0)
    if not added:
        return Answer(f"Nothing new {_phrase(window)}.", "additions", s)

    bits = [f"You added {_plural(added, 'thing')} {_phrase(window)}"]
    rev = t.get("revisions", 0)
    if rev == 1:
        bits.append("one a revision of something you already had")
    elif rev > 1:
        bits.append(f"{rev} of them revisions of things you already had")
    dup = t.get("duplicate", 0)
    if dup == 1:
        bits.append("one an exact duplicate, skipped")
    elif dup > 1:
        bits.append(f"{dup} exact duplicates, skipped")
    line = ", ".join(bits) + "."

    names = list(dict.fromkeys(s["names"]))     # dedupe, keep order
    if names:
        shown = names[:8]
        line += "\n  " + "\n  ".join(shown)
        if len(names) > len(shown):
            line += f"\n  ... and {len(names) - len(shown)} more"
    return Answer(line, "additions", s)


@intent("interests",
        r"\bwhat.*(have|'ve) i been (into|reading|working|looking)",
        r"\bwhat.*(am|was) i (into|interested)",
        r"\bwhat.*on my mind\b", r"\bwhat.*been about\b",
        r"\bwhat.*i been up to\b")
def _interests(bag, q: str) -> Answer:
    """What the fast weights have been pulled toward lately.

    This is the one question that only exists because of the two-timescale
    design: `w_fast` literally is 'what I have been asked about lately',
    separate from what the corpus stably is.
    """
    window = _window(q, "fortnight")
    since = bag.corpus.since(window)
    queries = []
    if bag.race.log_path.exists():
        import json
        with open(bag.race.log_path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("at", 0) >= since and rec.get("query"):
                    queries.append(rec["query"])
    if not queries:
        return Answer(f"You haven't searched for anything {_phrase(window)}.",
                      "interests")

    from collections import Counter
    from .clerk import tokens
    words = Counter()
    for qq in queries:
        for w in tokens(qq):
            if len(w) > 3:
                words[w] += 1
    top = [w for w, _ in words.most_common(8)]
    n = len(queries)
    line = (f"{_plural(n, 'search')} {_phrase(window)}, "
            f"mostly around: {', '.join(top)}." if top
            else f"{_plural(n, 'search')} {_phrase(window)}.")
    return Answer(line, "interests", {"queries": len(queries), "terms": top})


@intent("gaps",
        r"\bwhat.*(am i|are we) missing\b", r"\bwhat don'?t i have\b",
        r"\bwhat.*couldn'?t.*find\b", r"\bwhat.*failed\b",
        r"\bgaps?\b", r"\bblind spots?\b")
def _gaps(bag, q: str) -> Answer:
    """Negative space: queries that came back empty, repeatedly.

    The map of holes. No file system will ever tell you what you don't have.
    """
    import json
    from collections import Counter
    if not bag.race.log_path.exists():
        return Answer("No searches logged yet, so no gaps to report.", "gaps")
    empty = Counter()
    with open(bag.race.log_path, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not rec.get("clerk_ids") and not rec.get("cortex_only_verified"):
                if rec.get("query"):
                    empty[rec["query"]] += 1
    repeated = [(qq, n) for qq, n in empty.most_common(20) if n > 1]

    # A query with no literal match is not necessarily a gap: it may be one
    # the Cortex has *learned* to answer -- which is the entire point of the
    # learned layer. Separate the two, or the codewords you deliberately
    # taught it get reported back to you as failures.
    gaps, learned = [], []
    for qq, n in repeated:
        (learned if _cortex_is_confident(bag, qq) else gaps).append((qq, n))

    parts = []
    if gaps:
        parts.append("Things you keep looking for and don't have:\n"
                     + "\n".join(f"  {n}x  {qq}" for qq, n in gaps[:10]))
    if learned:
        parts.append("No literal match, but I've learned to answer these:\n"
                     + "\n".join(f"  {n}x  {qq}" for qq, n in learned[:10]))
    if parts:
        return Answer("\n\n".join(parts), "gaps",
                      {"gaps": gaps, "learned": learned})
    if empty:
        return Answer(
            f"{_plural(len(empty), 'search')} came back empty, but none more "
            f"than once. Nothing you keep reaching for and missing.",
            "gaps", {"empty": len(empty)})
    return Answer("Nothing you've looked for has come back empty.", "gaps")


def _cortex_is_confident(bag, query: str, threshold: float = 0.35) -> bool:
    """Has the learned layer actually been *taught* this query?

    Ask the fast weights alone, not the blended score. `w_slow` starts as the
    chunk's own content signature, so every query has some chunk that happens
    to align with it -- scoring against the blend measures coincidence, not
    learning. (An earlier version compared the top score against the spread
    and reported nonsense: in any 60-sample distribution something is always
    two standard deviations out.)

    `w_fast` is exactly zero until an intron is reinforced. A non-trivial
    cosine here means someone taught this, which is precisely the question.
    """
    import numpy as np
    if len(bag.cortex) == 0:
        return False
    q = bag.cortex.encoder(query)
    taught = bag.cortex.w_fast @ q
    return bool(len(taught)) and float(np.max(taught)) > threshold


@intent("whereabouts",
        r"\bwhat'?s? (on|in) the\b.*\b(drive|disk|vault|stick|ssd)\b",
        r"\bwhere is\b", r"\bwhat can'?t i (open|reach|see)\b",
        r"\bunreachable\b", r"\bwhat'?s? unplugged\b",
        r"\bwhich (drive|vault)\b")
def _whereabouts(bag, q: str) -> Answer:
    """[1b] What we know about but cannot currently open."""
    missing = bag.unreachable()
    vaults = bag.ledger.vaults
    if not missing:
        v = (f" {_plural(len(vaults), 'vault')} attached." if vaults else "")
        return Answer(f"Everything in the bag is reachable right now.{v}",
                      "whereabouts")
    from collections import Counter
    by_where = Counter(m["where"] for m in missing)
    lines = [f"  {n} in {where}" for where, n in by_where.most_common()]
    hint = next((m.get("hint") for m in missing if m.get("hint")), "")
    body = (f"{_plural(len(missing), 'thing')} you know about but can't open "
            f"right now:\n" + "\n".join(lines))
    if hint:
        body += f"\n  -> {hint}"
    body += "\n  (they're still searchable -- you just can't read the bytes)"
    return Answer(body, "whereabouts", {"unreachable": len(missing)})


@intent("history",
        r"\bshow me the (history|log|timeline)\b", r"\bwhat happened\b",
        r"\btimeline\b", r"\bhistory\b")
def _history(bag, q: str) -> Answer:
    window = _window(q, "month")
    recs = bag.corpus.records(since=bag.corpus.since(window))
    if not recs:
        return Answer(f"Nothing recorded {_phrase(window)}.", "history")
    lines = []
    for r in recs[-12:]:
        when = time.strftime("%d %b %H:%M", time.localtime(r["at"]))
        ev = r["event"]
        if ev == "ingest":
            lines.append(f"  {when}  +{r.get('ingested', 0)} added")
        elif ev == "dream":
            lines.append(f"  {when}  dreamt, {r.get('towers', 0)} towers")
        else:
            lines.append(f"  {when}  {ev}")
    return Answer(f"What happened {_phrase(window)}:\n" + "\n".join(lines),
                  "history", {"events": len(recs)})


@intent("size",
        r"\bhow (big|much|many)\b", r"\bwhat.*in (the bag|here)\b",
        r"\bsize of\b", r"\bhow full\b")
def _size(bag, q: str) -> Answer:
    st = bag.status()
    led, cx = st["ledger"], st["cortex"]
    mb = led["bytes"] / 1e6
    line = (f"{_plural(led['entries'], 'thing')}, {mb:,.1f} MB of content, "
            f"{cx.get('introns', 0):,} introns "
            f"({cx.get('bytes', 0) / 1e6:,.1f} MB of index).")
    rev = led.get("revisions", 0)
    if rev == 1:
        line += " One is a revision of an earlier version."
    elif rev > 1:
        line += f" {rev} are revisions of earlier versions."
    if st.get("unreachable"):
        line += f" {st['unreachable']} currently unreachable."
    return Answer(line, "size", st)


@intent("versions",
        r"\bversions? of\b", r"\bhistory of\b.*\bfile\b",
        r"\bhow.*changed\b", r"\bearlier versions?\b")
def _versions(bag, q: str) -> Answer:
    """[1a] Lineage made visible."""
    revised = [e for e in bag.ledger.entries() if e.parent_id]
    if not revised:
        return Answer("Nothing in the bag has been revised yet.", "versions")
    from collections import Counter
    names = Counter(e.name for e in revised)
    lines = [f"  {n + 1} versions  {name}" for name, n in names.most_common(10)]
    return Answer("Things you've revised:\n" + "\n".join(lines),
                  "versions", {"revised": len(revised)})


# --- the router ----------------------------------------------------------

_GREETING = re.compile(r"^\s*(hey |hi |ok |okay )?hermione[,:!]?\s*", re.I)


def ask(bag, question: str) -> Answer:
    """Route a question to a computed answer, or fall back to search."""
    q = _GREETING.sub("", question).strip()
    if not q:
        return Answer("I'm here. Ask me what you added this week, what "
                      "you've been into, or what you're missing.", "hello")

    for name, patterns, fn in _INTENTS:
        if any(p.search(q) for p in patterns):
            return fn(bag, q)

    # No known shape. Don't guess -- search, and say that's what happened.
    results, outcome = bag.find(q, limit=5)
    if not results:
        return Answer("I don't have a stock answer for that, and searching "
                      "for it found nothing.", "fallback_empty")
    lines = [f"  {r.name}" for r in results]
    return Answer("I don't have a stock answer for that, so I searched:\n"
                  + "\n".join(lines), "fallback_search",
                  {"hits": len(results)})


def known_intents() -> List[str]:
    return [name for name, _, _ in _INTENTS]
