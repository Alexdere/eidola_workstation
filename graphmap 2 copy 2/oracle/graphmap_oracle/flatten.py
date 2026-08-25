"""Flattening — spec/CORE-FORMAT.md §2 and §3.

A nested graph becomes leaf sites addressed by PATH, plus the resolved producer of every
consumer port. Container boundary ports survive as relay endpoints: a value crossing into a
composite crosses a real seam rather than being inlined away.
"""

from __future__ import annotations

import re

from .frames import FrameRegistry
from .model import IN, OUT, find_port

OCC_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class GraphError(Exception):
    pass


def ep(path: str, port_name: str) -> str:
    return f"{path}#{port_name}"


def ep_path(e: str) -> str:
    return e[: e.rindex("#")]


def ep_port(e: str) -> str:
    return e[e.rindex("#") + 1:]


def _join(prefix: str, occ: str) -> str:
    return f"{prefix}/{occ}" if prefix else occ


class Flat:
    def __init__(self) -> None:
        self.sites: dict[str, dict] = {}       # path -> {path, unit, pane}
        self.ep_port: dict[str, dict] = {}     # EP -> port decl
        self.links: list[dict] = []
        self.src_of: dict[str, dict] = {}      # consumer EP -> link
        self.resolved: dict[str, dict] = {}    # leaf in EP -> {producer, route}
        self.order: list[str] = []
        # container paths whose pane is pinned. A pin is a declaration ON A SEAM that what is
        # behind it has a reason to run, so it must reach the interior.
        self.pinned_prefixes: list[str] = []


def flatten(root: dict, units: dict[str, dict], frames: FrameRegistry) -> Flat:
    f = Flat()
    owners: dict[str, str] = {}

    def unit_of(pane: dict) -> dict:
        u = units.get(pane["unit"])
        if u is None:
            raise GraphError(f"unknown unit: {pane['unit']}")
        return u

    def seed(g: dict, prefix: str) -> None:
        for occ, pn in g["panes"].items():
            path = _join(prefix, occ)
            u = unit_of(pn)
            if u["cls"] == "container" and u["graph"]:
                owners[path] = pn["unit"]
                for p in u["ports"]:
                    f.ep_port[ep(path, p["name"])] = p
                seed(u["graph"], path)

    def check_endpoint(g: dict, prefix: str, endpoint: list, side: str) -> None:
        occ, port_name = endpoint
        if occ in (IN, OUT):
            owner = owners.get(prefix)
            if owner is None:
                raise GraphError(f"no owning composite for {prefix}")
            u = units[owner]
            want = "in" if occ == IN else "out"
            if occ == IN and side != "from":
                raise GraphError("@in may only be a wire source")
            if occ == OUT and side != "to":
                raise GraphError("@out may only be a wire target")
        else:
            u = unit_of(g["panes"][occ])
            want = "out" if side == "from" else "in"
        p = find_port(u, port_name)
        if p is None:
            raise GraphError(f"no port {port_name} on {occ}")
        if p["dir"] != want:
            raise GraphError(f"port {occ}.{port_name} is {p['dir']}, expected {want}")

    def endpoint_ep(prefix: str, endpoint: list) -> str:
        occ, port_name = endpoint
        if occ in (IN, OUT):
            return ep(prefix, port_name)
        return ep(_join(prefix, occ), port_name)

    def walk(g: dict, prefix: str, depth: int) -> None:
        if depth > 64:
            raise GraphError("graph nested too deeply")
        for occ, pn in g["panes"].items():
            if not OCC_RE.match(occ):
                raise GraphError(f"bad occurrence name: {occ}")
            if occ in (IN, OUT):
                raise GraphError(f"{occ} is reserved")
            path = _join(prefix, occ)
            u = unit_of(pn)
            for p in u["ports"]:
                f.ep_port[ep(path, p["name"])] = p
            if u["cls"] == "container":
                if u["powers"]:
                    raise GraphError(f"container {path} declares powers")
                if not u["graph"]:
                    raise GraphError(f"container {path} has no interior")
                if pn["mem"] is not None:
                    raise GraphError(f"container {path} names a mem address")
                if pn["pinned"]:
                    f.pinned_prefixes.append(path)
                walk(u["graph"], path, depth + 1)
            else:
                if not u["body"]:
                    raise GraphError(f"leaf {path} has no body")
                wants_mem = any(p.startswith("mem:") for p in u["powers"])
                if wants_mem != (pn["mem"] is not None):
                    raise GraphError(f"{path}: mem address / mem power mismatch")
                f.sites[path] = {"path": path, "unit": u, "pane": pn}

        for w in g["wires"]:
            for endpoint in (w["from"], w["to"]):
                occ = endpoint[0]
                if occ in (IN, OUT):
                    if not prefix:
                        raise GraphError(f"{occ} used in the root graph, which has no boundary")
                elif occ not in g["panes"]:
                    raise GraphError(f"wire endpoint names no pane: {occ}")
            check_endpoint(g, prefix, w["from"], "from")
            check_endpoint(g, prefix, w["to"], "to")
            f.links.append({"from": endpoint_ep(prefix, w["from"]),
                            "to": endpoint_ep(prefix, w["to"])})

    seed(root, "")
    walk(root, "", 0)

    # fan-in is not a thing; fan-out is
    for l in f.links:
        if l["to"] in f.src_of:
            raise GraphError(f"two wires into {l['to']}")
        f.src_of[l["to"]] = l

    # shapes and frames, at wire time
    for l in f.links:
        a = f.ep_port.get(l["from"])
        b = f.ep_port.get(l["to"])
        if a is None or b is None:
            raise GraphError(f"wire endpoint has no port: {l['from']} -> {l['to']}")
        if a["shape"] != "any" and b["shape"] != "any" and a["shape"] != b["shape"]:
            raise GraphError(f"shape mismatch {l['from']} -> {l['to']}")
        if not frames.connectable(a["frame"], b["frame"]):
            raise GraphError(f"no frame route {a['frame']} -> {b['frame']}")

    # resolve every leaf input to the leaf output that actually produces it
    for site in f.sites.values():
        for p in site["unit"]["ports"]:
            if p["dir"] != "in":
                continue
            consumer = ep(site["path"], p["name"])
            hop = f.src_of.get(consumer)
            route: list[tuple[str, str]] = []
            guard = 0
            while hop is not None:
                guard += 1
                if guard > 4096:
                    raise GraphError(f"cyclic wiring at {consumer}")
                a = f.ep_port[hop["from"]]
                b = f.ep_port[hop["to"]]
                if a["frame"] != b["frame"] and a["frame"] and b["frame"]:
                    route = frames.canonical_route(a["frame"], b["frame"]) + route
                from_path = ep_path(hop["from"])
                from_site = f.sites.get(from_path)
                if from_site is not None:
                    fp = find_port(from_site["unit"], ep_port(hop["from"]))
                    if fp is not None and fp["dir"] == "out":
                        f.resolved[consumer] = {"producer": hop["from"], "route": route}
                        break
                hop = f.src_of.get(hop["from"])   # a relay; keep walking
            # no producer -> absent. Absence is a member of the set, not an error.

    f.order = _topo(f)
    return f


def _topo(f: Flat) -> list[str]:
    """Edges incident to delay sites are cut: inside one tick the graph is always a DAG.
    A cycle without a delay is refused HERE, not discovered mid-tick."""
    deps: dict[str, set[str]] = {p: set() for p in f.sites}
    for consumer_ep, r in f.resolved.items():
        consumer = ep_path(consumer_ep)
        producer = ep_path(r["producer"])
        if f.sites[consumer]["unit"]["cls"] == "delay":
            continue
        if f.sites[producer]["unit"]["cls"] == "delay":
            continue
        deps[consumer].add(producer)

    out: list[str] = []
    state: dict[str, int] = {}

    def visit(p: str, stack: list[str]) -> None:
        s = state.get(p, 0)
        if s == 2:
            return
        if s == 1:
            cyc = stack[stack.index(p):] + [p]
            raise GraphError("cycle without a delay: " + " -> ".join(cyc))
        state[p] = 1
        stack.append(p)
        for d in sorted(deps[p]):
            visit(d, stack)
        stack.pop()
        state[p] = 2
        out.append(p)

    for p in sorted(f.sites):
        visit(p, [])
    return out
