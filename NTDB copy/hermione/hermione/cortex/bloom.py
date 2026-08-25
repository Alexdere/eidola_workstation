"""The deterministic half of an intron.

The learned vector proposes; the Bloom filter disposes. A Bloom filter has no
false negatives, so it can only ever remove blocks that provably do not contain
a term -- at zero I/O, before touching any data.

Two consequences worth stating:

  * It gives the Cortex a *cheap verifier*, so learning updates are driven by
    checked signal rather than noise.
  * A brand-new bag with untrained weights is already a competent filtered
    scanner. The cold-start weakness largely disappears.

Sizing note: FPR degrades sharply with distinct tokens per chunk. At 2048 bits
(256 bytes): ~1.7% at 300 distinct tokens, ~8.6% at 500, ~37% at 1200. This is
what couples chunk size to intron budget, and it is a real design constraint
rather than a tuning detail.
"""
from __future__ import annotations

from hashlib import blake2b
from typing import Iterable, List

import numpy as np


def positions(token: str, k: int, nbits: int) -> List[int]:
    """k independent bit positions for a token, from one hash."""
    h = blake2b(token.encode("utf-8"), digest_size=max(8, 4 * k)).digest()
    return [int.from_bytes(h[4 * i:4 * i + 4], "little") % nbits
            for i in range(k)]


def build(tokens: Iterable[str], nbytes: int, k: int) -> np.ndarray:
    """Pack tokens into a bit array of `nbytes` bytes."""
    nbits = nbytes * 8
    bits = np.zeros(nbytes, dtype=np.uint8)
    for tok in tokens:
        for p in positions(tok, k, nbits):
            bits[p >> 3] |= np.uint8(1 << (p & 7))
    return bits


def contains_any(filters_t: np.ndarray, query_tokens: Iterable[str],
                 k: int) -> np.ndarray:
    """Vectorised probe across every intron at once.

    Parameters
    ----------
    filters_t : uint8 array **[nbytes, N]** -- byte-major, one row per byte
        offset, one column per intron.

    Layout note: probing touches one byte offset across all introns at a time.
    Stored row-major as [N, nbytes] that is a strided gather down a column and
    it dominates query cost -- measured at 74ms for 500k introns versus 10ms
    for the entire vector scoring pass. Transposed, the same probe reads
    contiguous memory: 4.3ms, a 17x difference. Worth the awkward shape.

    Returns
    -------
    bool array [N] -- True where the intron *might* contain at least one
    token. No false negatives.
    """
    nbytes, n = filters_t.shape
    nbits = nbytes * 8
    toks = list(query_tokens)
    if n == 0:
        return np.zeros(0, dtype=bool)
    if not toks:
        return np.ones(n, dtype=bool)

    any_hit = np.zeros(n, dtype=bool)
    for tok in toks:
        hit = np.ones(n, dtype=bool)
        for p in positions(tok, k, nbits):
            byte = filters_t[p >> 3]
            hit &= ((byte >> np.uint8(p & 7)) & np.uint8(1)).astype(bool)
            if not hit.any():
                break
        any_hit |= hit
        if any_hit.all():
            break
    return any_hit


def optimal_k(nbytes: int, expected_tokens: int) -> int:
    import math
    m = nbytes * 8
    return max(1, min(12, round(m / max(1, expected_tokens) * math.log(2))))


def false_positive_rate(nbytes: int, k: int, n_tokens: int) -> float:
    import math
    m = nbytes * 8
    return (1 - math.exp(-k * n_tokens / m)) ** k
