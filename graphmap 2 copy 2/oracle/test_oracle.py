"""Tests for the rules that are the ORACLE's to get right — the ones that only exist because
Python has types JavaScript does not. The shared behaviour is tested by conform.py (static)
and grade.py (differential); duplicating it here would only test the transcription."""

from __future__ import annotations

import math
import sys

from graphmap_oracle import (
    CanonError, Engine, PowerDenied, canon_hex, default_frames, flatten, GraphError,
    load_workspace, prim_unit, unit_id, h,
)
from graphmap_oracle.prims import Ctx

FAILURES: list[str] = []


def check(name, fn):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        FAILURES.append(f"{name}: {type(e).__name__}: {e}")
        print(f"  x {name}")
        return
    print(f"  . {name}")


# ---------------------------------------------------------------- Python-only canon rules

def t_bool_before_int():
    # isinstance(True, int) is True in Python. A canonical encoding must not inherit that.
    assert canon_hex(True) == "74"
    assert canon_hex(1) != canon_hex(True)


def t_int_float_unified():
    # one numeric type: the deliberate narrowing on the Python side
    assert canon_hex(1) == canon_hex(1.0)
    assert h(3) == h(3.0)


def t_exact_int_or_refuse():
    assert canon_hex(2 ** 53 - 1)                       # exactly representable
    assert canon_hex(10 ** 20)                          # also exact — and a value
    try:
        canon_hex(2 ** 53 + 1)                          # not representable: must refuse
    except CanonError:
        return
    raise AssertionError("2^53+1 was silently rounded")


def t_neg_zero_and_nan():
    assert canon_hex(-0.0) == canon_hex(0.0)
    assert canon_hex(float("nan")) == "64" + "7ff8000000000000"
    assert canon_hex(math.inf) != canon_hex(-math.inf)


def t_utf8_key_order():
    a = {"Ｚ": 1, "\U0001D400": 2}
    b = {"\U0001D400": 2, "Ｚ": 1}
    assert canon_hex(a) == canon_hex(b)
    assert canon_hex(a).index("efbcba") < canon_hex(a).index("f09d9080")


def t_duplicate_and_bad_keys():
    try:
        canon_hex({1: "x"})                              # non-string key
    except CanonError:
        pass
    else:
        raise AssertionError("non-string key accepted")


# ---------------------------------------------------------------- law-shaped checks

def t_powers():
    ctx = Ctx(set(), None, lambda: [], lambda v: None, None, lambda v: None, False)
    for fn in (lambda: ctx.mem_fold("last", 0), lambda: ctx.mem_append(1),
               ctx.journal, lambda: ctx.world(1)):
        try:
            fn()
        except PowerDenied:
            continue
        raise AssertionError("an undeclared power was granted")
    assert not hasattr(ctx, "now")                        # time is a source or nothing


def t_route_agreement():
    assert default_frames().check_route_agreement() == []


def t_cycle_refused():
    ws = {
        "@": "workspace/1",
        "units": {"k": {"prim": "core.const"}, "add": {"prim": "core.add"}},
        "root": {
            "panes": {"c": {"unit": "k"}, "s": {"unit": "add"}},
            "wires": [[["c", "out"], ["s", "b"]], [["s", "out"], ["s", "a"]]],
        },
    }
    root, units, _ = load_workspace(ws)
    try:
        flatten(root, units, default_frames())
    except GraphError as e:
        assert "cycle" in str(e)
        return
    raise AssertionError("a cycle without a delay was accepted")


def t_primitive_unit_ids_are_stable():
    # the shared ground: an op name denotes one unit, and it must not drift
    assert unit_id(prim_unit("core.add")) == unit_id(prim_unit("core.add"))
    assert unit_id(prim_unit("core.add")) != unit_id(prim_unit("core.sub"))


def main() -> int:
    print("oracle:")
    for name, fn in sorted(globals().items()):
        if name.startswith("t_"):
            check(name[2:].replace("_", " "), fn)
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("all green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
