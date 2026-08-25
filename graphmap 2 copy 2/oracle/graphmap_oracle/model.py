"""CORE/1 objects. Implemented from spec/CORE-FORMAT.md §1.

Objects are plain dicts in exactly the spec's shape, so `canon` applies to them directly and
there is no separate "serialisation layer" to disagree with the spec.
"""

from __future__ import annotations

from typing import Any

from .canon import h

IN = "@in"
OUT = "@out"


def port(name: str, dir_: str, shape: str, frame: str | None = None) -> dict:
    return {"@": "port/1", "name": name, "dir": dir_, "shape": shape, "frame": frame}


def leaf(cls: str, body: dict, ports: list[dict], powers: list[str]) -> dict:
    return {
        "@": "unit/1",
        "ports": ports,
        "powers": sorted(powers),
        "cls": cls,
        "body": body,
        "graph": None,
    }


def container(ports: list[dict], graph: dict) -> dict:
    # Law 4: no composite may bind a power — the constructor has no parameter for one
    return {"@": "unit/1", "ports": ports, "powers": [], "cls": "container",
            "body": None, "graph": graph}


def pane(unit_id: str, config: Any = None, mem: str | None = None,
         pinned: bool = False) -> dict:
    return {"@": "pane/1", "unit": unit_id, "config": config, "mem": mem, "pinned": pinned}


def wire(a: tuple[str, str], b: tuple[str, str]) -> dict:
    return {"@": "wire/1", "from": [a[0], a[1]], "to": [b[0], b[1]]}


def graph(panes: dict[str, dict], wires: list[dict]) -> dict:
    return {"@": "graph/1", "panes": panes, "wires": wires}


def canonical_graph(g: dict) -> dict:
    """Wires sorted (authoring order is not content); ports untouched (a boundary is an
    ordered interface)."""
    ws = [{"@": w["@"], "from": list(w["from"]), "to": list(w["to"])} for w in g["wires"]]
    ws.sort(key=h)
    return {"@": "graph/1", "panes": g["panes"], "wires": ws}


def canonical_unit(u: dict) -> dict:
    return {
        "@": "unit/1",
        "ports": u["ports"],
        "powers": u["powers"],
        "cls": u["cls"],
        "body": u["body"],
        "graph": canonical_graph(u["graph"]) if u["graph"] else None,
    }


def unit_id(u: dict) -> str:
    return "unit:" + h(canonical_unit(u))


def graph_hash(g: dict) -> str:
    return h(canonical_graph(g))


def find_port(u: dict, name: str) -> dict | None:
    for p in u["ports"]:
        if p["name"] == name:
            return p
    return None
