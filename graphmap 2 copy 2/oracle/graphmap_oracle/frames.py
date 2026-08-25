"""Law 7 — frames, transitions, and the route-agreement check (finding #5)."""

from __future__ import annotations

import math
from typing import Any, Callable

from .canon import same_value


class FrameRegistry:
    def __init__(self) -> None:
        self.frames: dict[str, dict] = {}
        self.trans: dict[tuple[str, str], Callable[[Any], Any]] = {}

    def add_frame(self, fid: str, tol: float, fixtures: list) -> "FrameRegistry":
        self.frames[fid] = {"id": fid, "tol": tol, "fixtures": fixtures}
        return self

    def add_transition(self, a: str, b: str, fn) -> "FrameRegistry":
        self.trans[(a, b)] = fn
        return self

    def routes(self, a: str, b: str, max_len: int = 4) -> list[list[tuple[str, str]]]:
        out: list[list[tuple[str, str]]] = []

        def walk(at: str, seen: set[str], acc: list[tuple[str, str]]) -> None:
            if len(acc) > max_len:
                return
            if at == b and acc:
                out.append(list(acc))
                return
            for (f, t) in self.trans:
                if f != at or t in seen:
                    continue
                seen.add(t)
                acc.append((f, t))
                walk(t, seen, acc)
                acc.pop()
                seen.discard(t)

        walk(a, {a}, [])
        return out

    def canonical_route(self, a: str, b: str) -> list[tuple[str, str]]:
        """Shortest, ties broken lexicographically by the sequence of frame ids. Agreement is
        only within tolerance, so which route is taken shows up in the last bits of sigma:
        the choice must be a property of the registry's content, never of registration
        order."""
        rs = self.routes(a, b)
        if not rs:
            return []
        rs.sort(key=lambda r: (len(r), " ".join(t for (_f, t) in r)))
        return rs[0]

    def connectable(self, a: str | None, b: str | None) -> bool:
        if a == b:
            return True
        if a is None or b is None:
            return False
        return len(self.routes(a, b)) > 0

    def apply(self, route: list[tuple[str, str]], v: Any) -> Any:
        for hop in route:
            v = self.trans[hop](v)
        return v

    def check_route_agreement(self) -> list[str]:
        bad: list[str] = []
        ids = list(self.frames)
        for a in ids:
            for b in ids:
                if a == b:
                    continue
                rs = self.routes(a, b)
                if len(rs) < 2:
                    continue
                tol = self.frames[b]["tol"]
                for fx in self.frames[a]["fixtures"]:
                    res = [self.apply(r, fx) for r in rs]
                    for i in range(1, len(res)):
                        if not _agree(res[0], res[i], tol):
                            bad.append(f"{a}->{b} disagrees on {fx}: {res[0]} vs {res[i]}")
        return bad


def _agree(a: Any, b: Any, tol: float) -> bool:
    if tol > 0 and isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= tol
    return same_value(a, b)


def _n(v: Any) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return math.nan
    return float(v)


def default_frames() -> FrameRegistry:
    fx = [-40.0, 0.0, 37.0, 100.0]
    r = FrameRegistry()
    r.add_frame("temp.c", 1e-9, fx).add_frame("temp.f", 1e-9, fx).add_frame("temp.k", 1e-9, fx)
    r.add_transition("temp.c", "temp.f", lambda v: _n(v) * 9 / 5 + 32)
    r.add_transition("temp.f", "temp.c", lambda v: (_n(v) - 32) * 5 / 9)
    r.add_transition("temp.c", "temp.k", lambda v: _n(v) + 273.15)
    r.add_transition("temp.k", "temp.c", lambda v: _n(v) - 273.15)
    r.add_transition("temp.f", "temp.k", lambda v: (_n(v) - 32) * 5 / 9 + 273.15)
    r.add_transition("temp.k", "temp.f", lambda v: (_n(v) - 273.15) * 9 / 5 + 32)
    return r
