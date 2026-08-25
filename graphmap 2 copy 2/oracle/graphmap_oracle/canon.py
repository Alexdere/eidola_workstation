"""CVE/1 — the canonical value encoding. Implemented from spec/CANON.md.

The value universe has ONE numeric type, binary64, so Python ints are converted to floats
and integers beyond +/-(2^53-1) are refused rather than silently rounded. `1` and `1.0` are
the same value here, which is a deliberate narrowing on the Python side and simply the truth
on the JavaScript side.
"""

from __future__ import annotations

import hashlib
import math
import struct
from typing import Any

DOMAIN = b"graphmap/cve1\x00"
CANONICAL_NAN = struct.pack(">Q", 0x7FF8000000000000)


class CanonError(Exception):
    pass


def _dec(n: int) -> bytes:
    return str(n).encode("ascii")


def _num(x: float) -> bytes:
    if math.isnan(x):
        return CANONICAL_NAN
    if x == 0.0:
        x = 0.0  # collapses -0.0
    return struct.pack(">d", x)


def _write(v: Any, out: list[bytes], depth: int) -> None:
    if depth > 512:
        raise CanonError("value nested too deeply (cycle?)")

    if v is None:
        out.append(b"z")
        return
    # bool before int: isinstance(True, int) is True in Python, and that is exactly the
    # kind of language-local fact a canonical encoding must not inherit
    if isinstance(v, bool):
        out.append(b"t" if v else b"f")
        return
    if isinstance(v, int):
        # the universe is binary64, so an exact integer must convert exactly or be refused —
        # never silently rounded. This is the only place the two languages differ in what
        # they must check, because JS has no exact integer type to lose precision from.
        x = float(v)
        if int(x) != v:
            raise CanonError(f"integer not exactly representable as binary64: {v}")
        out.append(b"d")
        out.append(_num(x))
        return
    if isinstance(v, float):
        out.append(b"d")
        out.append(_num(v))
        return
    if isinstance(v, str):
        u = v.encode("utf-8")
        out.append(b"s" + _dec(len(u)) + b":" + u)
        return
    if isinstance(v, (bytes, bytearray)):
        b = bytes(v)
        out.append(b"x" + _dec(len(b)) + b":" + b)
        return
    if isinstance(v, (list, tuple)):
        out.append(b"l" + _dec(len(v)) + b";")
        for item in v:
            _write(item, out, depth + 1)
        return
    if isinstance(v, dict):
        items = []
        for k in v:
            if not isinstance(k, str):
                raise CanonError("map key must be a string")
            items.append((k.encode("utf-8"), v[k]))
        # sort by UTF-8 BYTE SEQUENCE, which is what `bytes` comparison already is
        items.sort(key=lambda kv: kv[0])
        for i in range(1, len(items)):
            if items[i - 1][0] == items[i][0]:
                raise CanonError("duplicate map key")
        out.append(b"m" + _dec(len(items)) + b";")
        for ku, val in items:
            out.append(b"k" + _dec(len(ku)) + b":" + ku)
            _write(val, out, depth + 1)
        return

    raise CanonError(f"not a value: {type(v).__name__}")


def canon(v: Any) -> bytes:
    out: list[bytes] = []
    _write(v, out, 0)
    return b"".join(out)


def canon_hex(v: Any) -> str:
    return canon(v).hex()


def h(v: Any) -> str:
    return hashlib.sha256(DOMAIN + canon(v)).hexdigest()


def same_value(a: Any, b: Any) -> bool:
    return canon(a) == canon(b)


def blob_id(v: Any) -> str:
    return "blob:" + h(v)
