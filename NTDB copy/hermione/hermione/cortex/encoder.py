"""Encoders: text -> unit vector.

The default is *signed feature hashing* (Weinberger et al. 2009), not a hash of
the whole string. This matters more than anything else in the Cortex:

    A hash of the whole query string has zero locality -- SHA-256 is designed
    so one changed character rewrites half the output. "alice email" and
    "alice address" would land maximally far apart, and the index could only
    ever respond to queries it had literally seen before. That is a lossy hash
    table wearing a learning rule.

    Hashing *features* instead means the two share the `alice` coordinate and
    have real cosine similarity. Character trigrams add typo and morphology
    tolerance. Cost: ~20us per query, no model, no download.

The registry exists so an embedding-backed encoder can be dropped in later
without touching the index. Any callable str -> np.ndarray[dim] works.
"""
from __future__ import annotations

import re
from hashlib import blake2b
from typing import Dict, Iterable, List  # noqa: F401

import numpy as np

_WORD = re.compile(r"\w+", re.UNICODE)
_REGISTRY: Dict[str, type] = {}


def register(name: str):
    def deco(cls):
        _REGISTRY[name] = cls
        return cls
    return deco


def get(name: str, **kw):
    if name not in _REGISTRY:
        raise KeyError(f"unknown encoder {name!r}; have {sorted(_REGISTRY)}")
    return _REGISTRY[name](**kw)


def words(text: str) -> List[str]:
    return [t.lower() for t in _WORD.findall(text)]


def features(text: str, use_trigrams: bool = True,
             min_trigram_len: int = 5) -> Iterable[str]:
    """Words, plus character trigrams of longer words."""
    for w in words(text):
        yield w
        if use_trigrams and len(w) >= min_trigram_len:
            padded = f"^{w}$"
            for i in range(len(padded) - 2):
                yield padded[i:i + 3]


@register("hashing")
class HashingEncoder:
    """Signed feature hashing into `dim` buckets.

    The sign trick makes collision noise zero-mean, so unrelated features that
    land in the same bucket cancel instead of accumulating a bias.
    """

    def __init__(self, dim: int = 128, use_trigrams: bool = True,
                 min_trigram_len: int = 5, **_):
        self.dim = dim
        self.use_trigrams = use_trigrams
        self.min_trigram_len = min_trigram_len

    def __call__(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for feat in features(text, self.use_trigrams, self.min_trigram_len):
            h = blake2b(feat.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "little") % self.dim
            v[idx] += 1.0 if (h[4] & 1) else -1.0
        n = float(np.linalg.norm(v))
        return v / n if n > 0 else v

    def batch(self, texts: Iterable[str]) -> np.ndarray:
        rows = [self(t) for t in texts]
        return (np.vstack(rows) if rows
                else np.zeros((0, self.dim), dtype=np.float32))
