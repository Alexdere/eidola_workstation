"""The primitive table — spec/CORE-FORMAT.md §6.

A primitive's identity is its op name, which is why a unit built from primitives has the same
hash in every conformant core. Arithmetic is binary64, so it is IEEE arithmetic in both
languages with nothing to negotiate.
"""

from __future__ import annotations

import math
from typing import Any, Callable

from .canon import same_value
from .model import leaf, port


class PowerDenied(Exception):
    pass


class Ctx:
    """Carries exactly the powers the boundary declares. Note the absence of a clock: time
    is not a power; a clock is a source or it does not exist."""

    def __init__(self, powers: set[str], config: Any, read_log: Callable[[], list],
                 append_log: Callable[[Any], None], journal_value: Any,
                 emit_world: Callable[[Any], None], has_journal: bool):
        self._powers = powers
        self.config = config
        self._read_log = read_log
        self._append_log = append_log
        self._journal_value = journal_value
        self._has_journal = has_journal
        self._emit_world = emit_world

    def _need(self, p: str) -> None:
        if p not in self._powers:
            raise PowerDenied(p)

    def mem_fold(self, fold: str, init: Any) -> Any:
        self._need("mem:r")
        log = self._read_log()
        if fold == "last":
            return log[-1] if log else init
        if fold == "all":
            return list(log)
        if fold == "sum":
            acc = float(init) if isinstance(init, (int, float)) and not isinstance(init, bool) else 0.0
            for v in log:
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    acc += float(v)
            return acc
        raise ValueError(f"unknown fold: {fold}")

    def mem_append(self, v: Any) -> None:
        self._need("mem:w")
        self._append_log(v)

    def journal(self):
        self._need("emit")
        return (self._journal_value, self._has_journal)

    def world(self, v: Any) -> None:
        self._need("world")
        self._emit_world(v)


def _n(v: Any) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return math.nan
    return float(v)


def _b(v: Any) -> bool:
    return v is True


PRIMS: dict[str, dict] = {}


def _p(op, cls, ports, powers, **fns):
    PRIMS[op] = {"op": op, "cls": cls, "ports": ports, "powers": powers, **fns}


_p("core.const", "pure", [port("out", "out", "any")], [],
   cook=lambda i, c: {"out": c.config})
_p("core.add", "pure",
   [port("a", "in", "num"), port("b", "in", "num"), port("out", "out", "num")], [],
   cook=lambda i, c: {"out": _n(i.get("a")) + _n(i.get("b"))})
_p("core.sub", "pure",
   [port("a", "in", "num"), port("b", "in", "num"), port("out", "out", "num")], [],
   cook=lambda i, c: {"out": _n(i.get("a")) - _n(i.get("b"))})
_p("core.mul", "pure",
   [port("a", "in", "num"), port("b", "in", "num"), port("out", "out", "num")], [],
   cook=lambda i, c: {"out": _n(i.get("a")) * _n(i.get("b"))})
_p("core.neg", "pure", [port("a", "in", "num"), port("out", "out", "num")], [],
   cook=lambda i, c: {"out": -_n(i.get("a"))})
_p("core.gt", "pure",
   [port("a", "in", "num"), port("b", "in", "num"), port("out", "out", "bool")], [],
   cook=lambda i, c: {"out": _n(i.get("a")) > _n(i.get("b"))})
_p("core.eq", "pure",
   [port("a", "in", "any"), port("b", "in", "any"), port("out", "out", "bool")], [],
   cook=lambda i, c: {"out": same_value(i.get("a"), i.get("b"))})
_p("core.not", "pure", [port("a", "in", "bool"), port("out", "out", "bool")], [],
   cook=lambda i, c: {"out": not _b(i.get("a"))})
_p("core.and", "pure",
   [port("a", "in", "bool"), port("b", "in", "bool"), port("out", "out", "bool")], [],
   cook=lambda i, c: {"out": _b(i.get("a")) and _b(i.get("b"))})
_p("core.or", "pure",
   [port("a", "in", "bool"), port("b", "in", "bool"), port("out", "out", "bool")], [],
   cook=lambda i, c: {"out": _b(i.get("a")) or _b(i.get("b"))})
_p("core.if", "pure",
   [port("c", "in", "bool"), port("a", "in", "any"), port("b", "in", "any"),
    port("out", "out", "any")], [],
   cook=lambda i, c: {"out": i.get("a") if _b(i.get("c")) else i.get("b")})
# non-strings become the empty string in BOTH cores: String(5) is "5" in JS and str(5.0) is
# "5.0" in Python, and a port of shape `any` can reach a `str` port
_p("core.concat", "pure",
   [port("a", "in", "str"), port("b", "in", "str"), port("out", "out", "str")], [],
   cook=lambda i, c: {"out": (i["a"] if isinstance(i.get("a"), str) else "")
                             + (i["b"] if isinstance(i.get("b"), str) else "")})
_p("core.list2", "pure",
   [port("a", "in", "any"), port("b", "in", "any"), port("out", "out", "list")], [],
   cook=lambda i, c: {"out": [i.get("a"), i.get("b")]})


def _source_cook(i, c):
    v, has = c.journal()
    return {"out": v} if has else {}


_p("core.source", "source", [port("out", "out", "any")], ["emit"], cook=_source_cook)

_p("core.delay", "delay",
   [port("x", "in", "any"), port("out", "out", "any")], ["mem:r", "mem:w"],
   read=lambda c: {"out": c.mem_fold("last", c.config)},
   write=lambda i, c: c.mem_append(i["x"]) if "x" in i else None)

_p("core.sum", "delay",
   [port("x", "in", "num"), port("out", "out", "num")], ["mem:r", "mem:w"],
   read=lambda c: {"out": c.mem_fold("sum", c.config if c.config is not None else 0.0)},
   write=lambda i, c: c.mem_append(i["x"]) if "x" in i else None)



def _minmax(a, b, want_min):
    a, b = _n(a), _n(b)
    # spelled out: Python's min()/max() return whichever argument came first when one is
    # NaN, while JS Math.min/max propagate NaN. The language default is not safe to inherit.
    if math.isnan(a) or math.isnan(b):
        return math.nan
    return (a if a < b else b) if want_min else (a if a > b else b)


def _floor(v):
    a = _n(v)
    # math.floor RAISES on NaN/Infinity in Python and returns an int; both are guarded
    return float(math.floor(a)) if math.isfinite(a) else a


def _at(i):
    a = i.get("a")
    if not isinstance(a, list):
        return None
    k = _n(i.get("i"))
    if not math.isfinite(k):
        return None
    k = int(math.floor(k))
    return a[k] if 0 <= k < len(a) else None


_p("core.lt", "pure",
   [port("a", "in", "num"), port("b", "in", "num"), port("out", "out", "bool")], [],
   cook=lambda i, c: {"out": _n(i.get("a")) < _n(i.get("b"))})
_p("core.abs", "pure", [port("a", "in", "num"), port("out", "out", "num")], [],
   cook=lambda i, c: {"out": abs(_n(i.get("a")))})
_p("core.min", "pure",
   [port("a", "in", "num"), port("b", "in", "num"), port("out", "out", "num")], [],
   cook=lambda i, c: {"out": _minmax(i.get("a"), i.get("b"), True)})
_p("core.max", "pure",
   [port("a", "in", "num"), port("b", "in", "num"), port("out", "out", "num")], [],
   cook=lambda i, c: {"out": _minmax(i.get("a"), i.get("b"), False)})
_p("core.floor", "pure", [port("a", "in", "num"), port("out", "out", "num")], [],
   cook=lambda i, c: {"out": _floor(i.get("a"))})
_p("core.len", "pure", [port("a", "in", "list"), port("out", "out", "num")], [],
   # lists only: JS string length counts UTF-16 units and Python counts code points, so
   # "length of a string" is not one concept and does not belong in the shared ground
   cook=lambda i, c: {"out": float(len(i["a"])) if isinstance(i.get("a"), list) else math.nan})
_p("core.at", "pure",
   [port("a", "in", "list"), port("i", "in", "num"), port("out", "out", "any")], [],
   cook=lambda i, c: {"out": _at(i)})
_p("core.append", "pure",
   [port("a", "in", "list"), port("x", "in", "any"), port("out", "out", "list")], [],
   cook=lambda i, c: {"out": (list(i["a"]) if isinstance(i.get("a"), list) else []) + [i.get("x")]})

# a delay whose fold is identity — which is what makes a chart of history an ordinary
# pattern rather than a feature
_p("core.hist", "delay",
   [port("x", "in", "any"), port("out", "out", "list")], ["mem:r", "mem:w"],
   read=lambda c: {"out": c.mem_fold("all", [])},
   write=lambda i, c: c.mem_append(i["x"]) if "x" in i else None)

_p("core.print", "effect", [port("x", "in", "any")], ["world"],
   act=lambda i, c: c.world(i.get("x")))


def prim_unit(op: str) -> dict:
    d = PRIMS.get(op)
    if d is None:
        raise KeyError(f"unknown primitive: {op}")
    return leaf(d["cls"], {"@": "prim/1", "op": op}, d["ports"], d["powers"])
