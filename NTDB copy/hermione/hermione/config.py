"""Configuration for a Hermione bag.

Everything tunable lives here. The config is persisted into the bag so that a
bag opened later behaves identically, and so that experiments are reproducible.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field, fields
from pathlib import Path


@dataclass
class Config:
    # --- Cortex geometry -------------------------------------------------
    dim: int = 128
    """Width of intron weight vectors. 128 is plenty at <1e6 introns; the
    analysis predicts no measurable quality loss vs 256 at half the cost."""

    bloom_bytes: int = 256
    """Per-intron Bloom filter size. FPR degrades sharply past ~300 distinct
    tokens per chunk, which is why chunk_chars is capped where it is."""

    bloom_k: int = 6
    """Hash count. Optimal for ~300 distinct tokens in 2048 bits."""

    bloom_bonus: float = 0.6
    """Score added when an intron *might* contain a query token. A bonus and
    not a gate: gating would prune exactly the cross-vocabulary queries the
    Cortex exists to answer. Lexical evidence outranks learned association;
    it never silences it. Set to 0 to disable the deterministic half."""

    # --- Encoder ---------------------------------------------------------
    encoder: str = "hashing"
    """Name in the encoder registry. Swappable: 'hashing' today, an embedding
    encoder later, without touching the index."""

    use_trigrams: bool = True
    """Character trigrams give fuzzy matching on typos/morphology for free."""

    min_trigram_len: int = 5
    """Only trigram-ify words at least this long, to limit feature blowup."""

    # --- Chunking --------------------------------------------------------
    chunk_chars: int = 1200
    """Target chunk size. One chunk == one intron == one retrievable block."""

    chunk_overlap: int = 120

    # --- Learning --------------------------------------------------------
    lr: float = 0.08
    """Learning rate for positive (teaching) updates on the fast weights."""

    lr_negative: float = 0.02

    alpha: float = 0.35
    """Blend: score = alpha * (q . w_fast) + (1 - alpha) * (q . w_slow).
    Fast = 'what I've been asked lately'. Slow = 'what I stably am'."""

    consolidate_tau: float = 0.15
    """Fraction of fast weights migrated into slow weights per consolidation."""

    fast_decay: float = 0.85
    """Applied to fast weights after consolidation (they are meant to fade)."""

    epsilon: float = 0.08
    """Exploration rate. Without this, introns outside the initial top-K are
    never read, never updated, and become permanently invisible."""

    top_k: int = 10
    """Candidates the Cortex returns per query."""

    # --- Ledger ----------------------------------------------------------
    copy_blobs: bool = True
    """Copy ingested files into the bag. The bag should never lose anything."""

    max_blob_mb: int = 512

    # --- Runtime ---------------------------------------------------------
    seed: int = 0

    extra: dict = field(default_factory=dict)
    """Free-form space for experiment flags, so subclassing isn't needed."""

    # ---------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> "Config":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))
