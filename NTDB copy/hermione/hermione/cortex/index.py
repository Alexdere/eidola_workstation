"""The Cortex: a learned intron layer over the chunks.

GENERATION 1   introns, fast/slow weights, bloom, cosine routing, towers
ITERATION 1a   + inheritance: an intron is a lineage, not a snapshot

One intron per chunk. Each carries:

    w_fast   high learning rate, high decay  -- "what I've been asked lately"
    w_slow   low learning rate, low decay    -- "what I stably am"
    bloom    exact membership, no false negatives
    stats    hits, misses, births

Scoring is cosine (weights are kept unit-norm). That is deliberate: with a raw
dot product, score scales with ||w||, so a frequently-queried intron starts
winning on loudness rather than fit, and under any skewed workload a handful of
introns capture the top-K for everything. Normalising removes the failure mode
entirely.

Candidate selection is epsilon-greedy. Without exploration, an intron outside
the initial top-K is never read, therefore never updated, therefore never
enters the top-K -- permanently invisible. That is an explore/exploit problem,
not a supervised one.

The whole structure is derived state: delete it, run `rebuild`, lose nothing
but time.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import bloom as bloom_mod
from . import encoder as encoder_mod

FORMAT_VERSION = 1


class Cortex:
    def __init__(self, root: Path, cfg):
        self.root = Path(root)
        self.cfg = cfg
        self.root.mkdir(parents=True, exist_ok=True)
        self.encoder = encoder_mod.get(
            cfg.encoder, dim=cfg.dim, use_trigrams=cfg.use_trigrams,
            min_trigram_len=cfg.min_trigram_len)
        self.rng = np.random.default_rng(cfg.seed)

        self.ids: List[str] = []
        self.pos: Dict[str, int] = {}
        d, b = cfg.dim, cfg.bloom_bytes
        self.w_fast = np.zeros((0, d), dtype=np.float32)
        self.w_slow = np.zeros((0, d), dtype=np.float32)
        self.blooms = np.zeros((b, 0), dtype=np.uint8)  # byte-major: [bytes, N]
        self.stats = np.zeros((0, 3), dtype=np.float32)  # hits, misses, born
        self._load()

    # -- persistence ------------------------------------------------------
    @property
    def _npz(self) -> Path:
        return self.root / "introns.npz"

    @property
    def _meta(self) -> Path:
        return self.root / "meta.json"

    def _load(self) -> None:
        if not (self._npz.exists() and self._meta.exists()):
            return
        meta = json.loads(self._meta.read_text())
        if meta.get("format") != FORMAT_VERSION or meta.get("dim") != self.cfg.dim:
            return  # incompatible: treat as empty, rebuild will repopulate
        z = np.load(self._npz)
        self.w_fast = z["w_fast"]
        self.w_slow = z["w_slow"]
        self.blooms = np.ascontiguousarray(z["blooms"])
        self.stats = z["stats"]
        self.ids = list(meta["ids"])
        self.pos = {cid: i for i, cid in enumerate(self.ids)}

    def save(self) -> None:
        np.savez_compressed(self._npz, w_fast=self.w_fast, w_slow=self.w_slow,
                            blooms=self.blooms, stats=self.stats)
        self._meta.write_text(json.dumps({
            "format": FORMAT_VERSION, "dim": self.cfg.dim,
            "bloom_bytes": self.cfg.bloom_bytes, "encoder": self.cfg.encoder,
            "ids": self.ids}))

    # -- growth -----------------------------------------------------------
    def add(self, chunk_ids: Sequence[str], texts: Sequence[str],
            inherit: Optional[Dict[str, str]] = None) -> int:   # [1a]
        """Create introns for new chunks. Idempotent on chunk_id.

        [1a] `inherit` maps new chunk_id -> predecessor chunk_id. Where a
        predecessor is given, the new intron starts from its learned weights
        instead of from its own content signature.

        This is the difference between an intron being a *snapshot* and being
        a *lineage*. Without it, editing a document silently throws away
        everything the Cortex learned about how you look for it -- no error,
        no warning, the system just quietly stops accumulating the one thing
        it exists to accumulate.
        """
        inherit = inherit or {}
        new_ids, new_texts = [], []
        for cid, txt in zip(chunk_ids, texts):
            if cid not in self.pos:
                new_ids.append(cid)
                new_texts.append(txt)
        if not new_ids:
            return 0

        # Slow weights default to the content's own signature -- the intron
        # begins knowing roughly what it holds, and learning refines it.
        seeds = self.encoder.batch(new_texts)
        fast = np.zeros_like(seeds)

        # [1a] ...unless there is a predecessor, in which case carry its
        # learning forward. The content seed is what an intron knows before
        # anyone has asked it anything; inherited weights are what it learned
        # from being asked.
        inherited = 0
        for row, cid in enumerate(new_ids):
            parent = inherit.get(cid)
            if parent is None:
                continue
            pi = self.pos.get(parent)
            if pi is None:
                continue
            seeds[row] = self.w_slow[pi]
            fast[row] = self.w_fast[pi]
            inherited += 1
        self.inherited_last = inherited

        blooms = np.stack([
            bloom_mod.build(set(encoder_mod.words(t)),
                            self.cfg.bloom_bytes, self.cfg.bloom_k)
            for t in new_texts], axis=1)          # -> [bytes, n_new]
        now = time.time()
        stats = np.zeros((len(new_ids), 3), dtype=np.float32)
        stats[:, 2] = now

        self.w_slow = np.vstack([self.w_slow, seeds])
        self.w_fast = np.vstack([self.w_fast, fast])
        self.blooms = np.ascontiguousarray(
            np.hstack([self.blooms, blooms]))
        self.stats = np.vstack([self.stats, stats])
        for cid in new_ids:
            self.pos[cid] = len(self.ids)
            self.ids.append(cid)
        return len(new_ids)

    # -- iteration 1a: finding the predecessor ----------------------------
    def match_chunks(self, old_ids: Sequence[str], old_texts: Sequence[str],
                     new_texts: Sequence[str],
                     threshold: float = 0.75) -> Dict[int, str]:
        """Map each new chunk (by row index) to its best predecessor chunk_id.

        Matching is on *content* signatures, deliberately: `w_slow` drifts as
        an intron learns, so a heavily-trained chunk would match its own
        revision poorly. What we want here is "is this the same paragraph,
        edited", which is a question about text, not about training.

        Cosine over content is robust to insertion and reordering in a way
        that matching by chunk ordinal is not -- add a paragraph at the top
        of a document and every ordinal shifts, but every similarity holds.
        """
        pairs = [(c, t) for c, t in zip(old_ids, old_texts) if c in self.pos]
        if not pairs or not len(new_texts):
            return {}
        usable = [c for c, _ in pairs]
        old = self.encoder.batch([t for _, t in pairs])   # [n_old, d]
        new = self.encoder.batch(list(new_texts))         # [n_new, d]
        sims = new @ old.T
        out: Dict[int, str] = {}
        for i in range(sims.shape[0]):
            j = int(np.argmax(sims[i]))
            if float(sims[i, j]) >= threshold:
                out[i] = usable[j]
        return out

    def __len__(self) -> int:
        return len(self.ids)

    # -- scoring ----------------------------------------------------------
    def score_all(self, qvec: np.ndarray) -> np.ndarray:
        if len(self) == 0:
            return np.zeros(0, dtype=np.float32)
        a = self.cfg.alpha
        return (a * (self.w_fast @ qvec) + (1 - a) * (self.w_slow @ qvec))

    def search(self, query: str, k: Optional[int] = None,
               explore: bool = True, use_bloom: bool = True
               ) -> List[Tuple[str, float]]:
        if len(self) == 0:
            return []
        k = k or self.cfg.top_k
        qvec = self.encoder(query)
        scores = self.score_all(qvec)

        if use_bloom and self.cfg.bloom_bonus:
            toks = [t for t in set(encoder_mod.words(query)) if len(t) > 1]
            if toks:
                mask = bloom_mod.contains_any(self.blooms, toks, self.cfg.bloom_k)
                # A *bonus*, not a gate. Gating would be tempting -- Bloom has
                # no false negatives, so a rejection is provably safe on
                # literal grounds. But the Cortex exists to answer queries
                # whose words never appear in the target text, and a gate
                # would prune exactly those. Lexical evidence should outrank
                # learned association, never silence it.
                scores = scores + self.cfg.bloom_bonus * mask

        n = len(scores)
        take = min(k, n)
        if take >= n:
            idx = np.argsort(-scores)
        else:
            part = np.argpartition(-scores, take - 1)[:take]
            idx = part[np.argsort(-scores[part])]

        picked = list(idx)
        if explore and self.cfg.epsilon > 0 and n > take:
            n_explore = int(self.rng.binomial(take, self.cfg.epsilon))
            if n_explore:
                pool = np.setdiff1d(np.arange(n), np.asarray(picked),
                                    assume_unique=False)
                if len(pool):
                    picked += list(self.rng.choice(
                        pool, size=min(n_explore, len(pool)), replace=False))

        return [(self.ids[i], float(scores[i])) for i in picked]

    # -- learning ---------------------------------------------------------
    def reinforce(self, query: str, positives: Sequence[str],
                  negatives: Sequence[str] = ()) -> None:
        """Move introns toward or away from a query, then renormalise.

        Renormalisation is what prevents the loud-intron failure mode.
        """
        if not positives and not negatives:
            return
        qvec = self.encoder(query)
        touched: List[int] = []

        for cid in positives:
            i = self.pos.get(cid)
            if i is None:
                continue
            self.w_fast[i] += self.cfg.lr * qvec
            self.stats[i, 0] += 1
            touched.append(i)

        for cid in negatives:
            i = self.pos.get(cid)
            if i is None:
                continue
            self.w_fast[i] -= self.cfg.lr_negative * qvec
            self.stats[i, 1] += 1
            touched.append(i)

        if touched:
            rows = np.unique(np.asarray(touched))
            norms = np.linalg.norm(self.w_fast[rows], axis=1, keepdims=True)
            # Fancy indexing copies on read, so assign the result back.
            self.w_fast[rows] = self.w_fast[rows] / np.maximum(norms, 1e-8)

    def consolidate(self) -> dict:
        """Sleep: migrate what the fast weights learned into the slow ones.

        Complementary Learning Systems, in miniature -- a single-timescale
        system cannot both absorb quickly and retain stably.
        """
        if len(self) == 0:
            return {"introns": 0, "moved": 0.0}
        tau = self.cfg.consolidate_tau
        active = np.linalg.norm(self.w_fast, axis=1) > 1e-6
        moved = float(active.sum())
        if moved:
            blended = (1 - tau) * self.w_slow[active] + tau * self.w_fast[active]
            norms = np.linalg.norm(blended, axis=1, keepdims=True)
            self.w_slow[active] = blended / np.maximum(norms, 1e-8)
            self.w_fast[active] *= self.cfg.fast_decay
        return {"introns": len(self), "moved": moved}

    # -- towers -----------------------------------------------------------
    def towers(self, k: int = 8, iters: int = 25) -> Dict[int, List[int]]:
        """k-means over slow weights. Towers are not declared, they precipitate."""
        n = len(self)
        if n == 0:
            return {}
        k = max(1, min(k, n))
        X = self.w_slow
        centroids = X[self.rng.choice(n, size=k, replace=False)].copy()
        assign = np.zeros(n, dtype=np.int64)
        for _ in range(iters):
            sims = X @ centroids.T
            new = np.argmax(sims, axis=1)
            if np.array_equal(new, assign):
                break
            assign = new
            for c in range(k):
                members = X[assign == c]
                if len(members):
                    v = members.mean(axis=0)
                    nv = np.linalg.norm(v)
                    if nv > 0:
                        centroids[c] = v / nv
        out: Dict[int, List[int]] = {}
        for c in range(k):
            members = np.flatnonzero(assign == c)
            if len(members):
                out[c] = members.tolist()
        return out

    def summary(self) -> dict:
        n = len(self)
        if n == 0:
            return {"introns": 0}
        fast_norm = np.linalg.norm(self.w_fast, axis=1)
        return {
            "introns": n,
            "dim": self.cfg.dim,
            "bytes_per_intron": int(
                (self.w_fast.nbytes + self.w_slow.nbytes
                 + self.blooms.nbytes) / max(1, n)),
            "bytes": int(self.w_fast.nbytes + self.w_slow.nbytes
                         + self.blooms.nbytes),
            "trained": int((fast_norm > 1e-6).sum()),
            "hits": float(self.stats[:, 0].sum()),
            "misses": float(self.stats[:, 1].sum()),
        }
