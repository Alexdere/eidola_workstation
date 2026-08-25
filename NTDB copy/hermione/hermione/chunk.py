"""Chunking: text -> blocks. One block == one intron.

Deliberately isolated behind a registry. "Where should the cut go?" is a
segmentation problem, and segmentation is learnable -- a learned chunker is a
planned successor to the paragraph-greedy one here. Nothing else in the system
should need to change when that lands.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Dict, List

_SPLIT = re.compile(r"\n\s*\n")


@dataclass
class Chunk:
    ord: int
    text: str
    char_start: int
    char_end: int


Chunker = Callable[[str, int, int], List[Chunk]]
_REGISTRY: Dict[str, Chunker] = {}


def chunker(name: str):
    def deco(fn: Chunker) -> Chunker:
        _REGISTRY[name] = fn
        return fn
    return deco


def get(name: str) -> Chunker:
    if name not in _REGISTRY:
        raise KeyError(f"unknown chunker {name!r}; have {sorted(_REGISTRY)}")
    return _REGISTRY[name]


@chunker("paragraph")
def paragraph_chunker(text: str, size: int = 1200,
                      overlap: int = 120) -> List[Chunk]:
    """Greedy pack paragraphs up to `size`, splitting oversized ones."""
    chunks: List[Chunk] = []
    buf: List[str] = []
    buf_len = 0
    start = 0
    cursor = 0

    def flush(end: int) -> None:
        nonlocal buf, buf_len, start
        if not buf:
            return
        body = "\n\n".join(buf).strip()
        if body:
            chunks.append(Chunk(len(chunks), body, start, end))
        buf, buf_len = [], 0
        start = max(0, end - overlap)

    for para in _SPLIT.split(text):
        para_start = text.find(para, cursor) if para else cursor
        if para_start < 0:
            para_start = cursor
        cursor = para_start + len(para)

        if len(para) > size:
            flush(para_start)
            for i in range(0, len(para), size - overlap):
                piece = para[i:i + size].strip()
                if piece:
                    chunks.append(Chunk(len(chunks), piece,
                                        para_start + i,
                                        para_start + min(i + size, len(para))))
            start = cursor
            continue

        if buf_len + len(para) > size:
            flush(para_start)
        buf.append(para)
        buf_len += len(para)

    flush(cursor)
    return [c for c in chunks if c.text.strip()]
