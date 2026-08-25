"""The corpus journal: the bag's memory of its own past.

ITERATION 1c   new in this iteration

`status` answers "what is in the bag right now". That is a snapshot, and a
snapshot cannot answer any of the questions people actually ask a personal
archive:

    what did I add this week?
    what have I been into lately?
    what did I have in March that I don't have now?

All of those are questions about *change*, and change needs a record. This is
that record: one append-only line per event, a few hundred bytes, forever.

Cheapest thing in the system and it converts several previously-speculative
features into arithmetic.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

DAY = 86400.0
WINDOWS = {
    "today": DAY,
    "yesterday": 2 * DAY,
    "week": 7 * DAY,
    "fortnight": 14 * DAY,
    "month": 30 * DAY,
    "quarter": 91 * DAY,
    "year": 365 * DAY,
    "ever": float("inf"),
}


class CorpusJournal:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: str, **fields) -> dict:
        rec = {"at": time.time(), "event": event, **fields}
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def records(self, since: Optional[float] = None,
                event: Optional[str] = None) -> List[dict]:
        if not self.path.exists():
            return []
        out = []
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if since is not None and rec.get("at", 0) < since:
                    continue
                if event is not None and rec.get("event") != event:
                    continue
                out.append(rec)
        return out

    def since(self, window: str = "week") -> float:
        span = WINDOWS.get(window, 7 * DAY)
        return 0.0 if span == float("inf") else time.time() - span

    def summarise(self, window: str = "week") -> dict:
        """Roll up ingest activity over a window."""
        recs = self.records(since=self.since(window), event="ingest")
        totals: Dict[str, int] = {}
        names: List[str] = []
        for r in recs:
            for k in ("ingested", "chunks", "introns", "duplicate",
                      "inherited", "revisions"):
                if r.get(k):
                    totals[k] = totals.get(k, 0) + int(r[k])
            names += r.get("names", [])
        return {"window": window, "events": len(recs), "totals": totals,
                "names": names}
