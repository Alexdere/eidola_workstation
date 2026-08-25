"""WORKSPACE/1 loader and VSPEC decoding. Implemented from the spec, so the oracle reads the
same file the TypeScript core does — neither owns the format."""

from __future__ import annotations

import struct
from typing import Any

from .model import container, graph, pane, port, unit_id, wire
from .prims import prim_unit


def from_spec(s: Any) -> Any:
    if s is None:
        return None
    if "n" in s:
        return None
    if "b" in s:
        return s["b"]
    if "d" in s:
        return struct.unpack(">d", bytes.fromhex(s["d"]))[0]
    if "s" in s:
        return s["s"]
    if "x" in s:
        return bytes.fromhex(s["x"])
    if "l" in s:
        return [from_spec(i) for i in s["l"]]
    if "m" in s:
        return {k: from_spec(v) for k, v in s["m"].items()}
    raise ValueError(f"bad vspec: {s}")


def to_spec(v: Any) -> dict:
    if v is None:
        return {"n": None}
    if isinstance(v, bool):
        return {"b": v}
    if isinstance(v, (int, float)):
        x = float(v)
        if x != x:
            return {"d": "7ff8000000000000"}
        if x == 0.0:
            x = 0.0
        return {"d": struct.pack(">d", x).hex()}
    if isinstance(v, str):
        return {"s": v}
    if isinstance(v, (bytes, bytearray)):
        return {"x": bytes(v).hex()}
    if isinstance(v, (list, tuple)):
        return {"l": [to_spec(i) for i in v]}
    if isinstance(v, dict):
        return {"m": {k: to_spec(x) for k, x in v.items()}}
    raise ValueError("not a value")


def load_workspace(ws: dict) -> tuple[dict, dict[str, dict], dict[str, str]]:
    """Returns (root_graph, units_by_id, local_name -> unit id)."""
    units: dict[str, dict] = {}
    ids: dict[str, str] = {}

    def build_unit(name: str, seen: set[str]) -> str:
        if name in ids:
            return ids[name]
        if name in seen:
            raise ValueError(f"unit cycle at {name}")
        seen.add(name)
        s = ws["units"].get(name)
        if s is None:
            raise ValueError(f"unknown unit name: {name}")
        if "prim" in s:
            u = prim_unit(s["prim"])
            frames = s.get("frames")
            if frames:
                u = dict(u, ports=[
                    dict(p, frame=frames[p["name"]]) if p["name"] in frames else p
                    for p in u["ports"]
                ])
        else:
            c = s["container"]
            ports = [port(n, d, sh, fr) for (n, d, sh, fr) in c["ports"]]
            u = container(ports, build_graph(c["graph"], seen))
        uid = unit_id(u)
        units[uid] = u
        ids[name] = uid
        seen.discard(name)
        return uid

    def build_graph(gs: dict, seen: set[str]) -> dict:
        panes = {}
        for occ, ps in gs["panes"].items():
            panes[occ] = pane(
                build_unit(ps["unit"], seen),
                config=from_spec(ps.get("config")),
                mem=ps.get("mem"),
                pinned=ps.get("pinned", False),
            )
        return graph(panes, [wire(tuple(a), tuple(b)) for (a, b) in gs["wires"]])

    root = build_graph(ws["root"], set())
    for name in ws["units"]:
        build_unit(name, set())
    return root, units, ids
