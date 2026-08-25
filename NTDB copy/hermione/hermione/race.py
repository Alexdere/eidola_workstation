"""The Race: both layers answer every query, and the disagreement is the signal.

    query --+-- Clerk  --> exact matches      (fast, literal, precise)
            +-- Cortex --> associations       (fuzzy, surprising)
                              |
                        merge, present, LOG THE DISAGREEMENT

    Clerk hit, Cortex missed        -> free labelled training example
    Clerk hit, Cortex hit           -> agreement, rank up
    Clerk missed, Cortex hit        -> the reason this product exists
    Both missed                     -> a hole; feed the negative-space map

The Clerk grades the Cortex's homework, forever, automatically. No thumbs
up/down, no hand-labelled query set, no synthetic benchmark to accidentally
rig. Every real query is a data point.

The Cortex's job is *not* to replace exact search. It is to answer the queries
exact search fails on: cross-format, cross-language, vague, half-remembered.
The single honest metric for whether it is earning its keep is
`cortex_only_verified` -- results it found that the Clerk could not.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .cortex.encoder import words


@dataclass
class Result:
    chunk_id: str
    score: float
    source: str            # clerk | cortex | both
    name: str = ""
    preview: str = ""
    rank_clerk: Optional[int] = None
    rank_cortex: Optional[int] = None


@dataclass
class RaceOutcome:
    query: str
    at: float
    clerk_ids: List[str] = field(default_factory=list)
    cortex_ids: List[str] = field(default_factory=list)
    overlap: List[str] = field(default_factory=list)
    cortex_only_verified: List[str] = field(default_factory=list)
    recall_at_k: float = 0.0
    ms_clerk: float = 0.0
    ms_cortex: float = 0.0

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def _verify(text: str, query: str) -> bool:
    """Cheap ground truth: does this chunk plausibly answer the query?

    Used only for chunks the Clerk did *not* return, to tell a genuine
    cross-format find apart from a hallucinated one.
    """
    if not text:
        return False
    hay = set(words(text))
    terms = [t for t in words(query) if len(t) > 2]
    if not terms:
        return False
    return sum(t in hay for t in terms) / len(terms) >= 0.5


class Race:
    def __init__(self, clerk, cortex, log_path: Path, *, learn: bool = True):
        self.clerk = clerk
        self.cortex = cortex
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.learn = learn

    def query(self, q: str, *, limit: int = 10, learn: Optional[bool] = None
              ) -> tuple[List[Result], RaceOutcome]:
        learn = self.learn if learn is None else learn

        t0 = time.perf_counter()
        clerk_hits = self.clerk.search(q, limit=max(limit, 20))
        t1 = time.perf_counter()
        cortex_hits = self.cortex.search(q, k=max(limit, 20))
        t2 = time.perf_counter()

        clerk_ids = [cid for cid, _ in clerk_hits]
        cortex_ids = [cid for cid, _ in cortex_hits]
        clerk_set, cortex_set = set(clerk_ids), set(cortex_ids)
        overlap = [c for c in cortex_ids if c in clerk_set]

        # Anything the Cortex proposed that the Clerk did not -- keep only what
        # actually verifies against the text. This is the value proposition,
        # and it must not be allowed to be noise.
        cortex_only_verified = []
        for cid in cortex_ids:
            if cid in clerk_set:
                continue
            if _verify(self.clerk.chunk_text(cid) or "", q):
                cortex_only_verified.append(cid)

        top_clerk = clerk_ids[:limit]
        recall = (len([c for c in top_clerk if c in cortex_set]) / len(top_clerk)
                  if top_clerk else 0.0)

        outcome = RaceOutcome(
            query=q, at=time.time(), clerk_ids=clerk_ids, cortex_ids=cortex_ids,
            overlap=overlap, cortex_only_verified=cortex_only_verified,
            recall_at_k=recall, ms_clerk=(t1 - t0) * 1e3,
            ms_cortex=(t2 - t1) * 1e3)

        if learn:
            self._teach(q, clerk_ids, cortex_ids, cortex_only_verified)
            self._log(outcome)

        return self._merge(q, clerk_hits, cortex_hits, limit), outcome

    # -- the teaching step -------------------------------------------------
    def _teach(self, q: str, clerk_ids: Sequence[str],
               cortex_ids: Sequence[str], cortex_only: Sequence[str]) -> None:
        """The Clerk's answers are labels. Use them."""
        clerk_set = set(clerk_ids)
        # Positive: everything the Clerk vouches for, whether or not the
        # Cortex found it. Introns the Cortex missed get pulled toward the
        # query -- that is the actual correction signal.
        positives = list(clerk_ids[:10]) + list(cortex_only)
        # Negative: Cortex proposed it, Clerk did not vouch, text does not
        # verify. A real miss.
        negatives = [c for c in cortex_ids
                     if c not in clerk_set and c not in set(cortex_only)]
        self.cortex.reinforce(q, positives, negatives[:10])

    def _log(self, outcome: RaceOutcome) -> None:
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(outcome.to_json() + "\n")

    # -- presentation ------------------------------------------------------
    def _merge(self, q: str, clerk_hits, cortex_hits, limit: int) -> List[Result]:
        by_id: Dict[str, Result] = {}
        for rank, (cid, score) in enumerate(clerk_hits):
            by_id[cid] = Result(cid, score, "clerk", rank_clerk=rank)
        for rank, (cid, score) in enumerate(cortex_hits):
            if cid in by_id:
                by_id[cid].source = "both"
                by_id[cid].rank_cortex = rank
            else:
                by_id[cid] = Result(cid, score, "cortex", rank_cortex=rank)

        results = list(by_id.values())

        def sort_key(r: Result):
            # Agreement first, then the Clerk's literal matches, then
            # associations. The magic layer is additive: it can add results,
            # never bury a literal match.
            tier = {"both": 0, "clerk": 1, "cortex": 2}[r.source]
            rank = r.rank_clerk if r.rank_clerk is not None else r.rank_cortex
            return (tier, rank if rank is not None else 999)

        results.sort(key=sort_key)
        results = results[:limit]

        for r in results:
            info = self.clerk.chunk_info(r.chunk_id)
            if info:
                r.name = info.get("name") or info.get("entry_id", "")
                r.preview = _preview(info["text"], q)
        return results

    # -- evaluation --------------------------------------------------------
    def replay(self, limit: Optional[int] = None) -> List[RaceOutcome]:
        """Re-run the logged queries without learning. The built-in benchmark."""
        if not self.log_path.exists():
            return []
        queries: List[str] = []
        seen = set()
        with open(self.log_path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                qs = rec.get("query")
                if qs and qs not in seen:
                    seen.add(qs)
                    queries.append(qs)
        if limit:
            queries = queries[-limit:]
        return [self.query(q, learn=False)[1] for q in queries]


def _preview(text: str, query: str, width: int = 220) -> str:
    lowered = text.lower()
    for term in sorted(words(query), key=len, reverse=True):
        idx = lowered.find(term)
        if idx >= 0:
            start = max(0, idx - width // 3)
            snippet = text[start:start + width].replace("\n", " ")
            return ("..." if start else "") + snippet.strip() + "..."
    return text[:width].replace("\n", " ").strip() + "..."
