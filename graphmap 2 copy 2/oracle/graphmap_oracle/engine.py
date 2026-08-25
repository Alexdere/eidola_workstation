"""The five-pass tick — spec/CORE-FORMAT.md §4.

The five classes are the five passes: source / delay-read / body / delay-write / commit.
That is the whole content of the taxonomy.
"""

from __future__ import annotations

from typing import Any

from .canon import blob_id, h
from .flatten import Flat, ep, ep_path, flatten
from .frames import FrameRegistry, default_frames
from .model import graph_hash
from .prims import PRIMS, Ctx


class Engine:
    def __init__(self, root: dict, units: dict[str, dict], frames: FrameRegistry | None = None):
        self.root = root
        self.units = units
        self.frames = frames or default_frames()
        self.f: Flat = flatten(root, units, self.frames)
        self.blobs: dict[str, Any] = {}
        self.mem: dict[str, list[str]] = {}
        self.memo: dict[str, dict[str, str]] = {}     # cache tier; never shipped
        self.last_effect_inputs: dict[str, str] = {}
        self.tick_index = 0

    def graph_hash(self) -> str:
        return graph_hash(self.root)

    def put_blob(self, v: Any) -> str:
        bid = blob_id(v)
        self.blobs.setdefault(bid, v)
        return bid

    # ------------------------------------------------------------------ liveness

    def _live_set(self, observed: list[str]) -> set[str]:
        f = self.f
        def pinned_by_ancestor(path: str) -> bool:
            return any(path == p or path.startswith(p + "/") for p in f.pinned_prefixes)

        live = {
            path for path, site in f.sites.items()
            if site["pane"]["pinned"] or pinned_by_ancestor(path)
            or any(o == path or path.startswith(o + "/") for o in observed)
        }
        grew = True
        while grew:
            grew = False
            for consumer_ep, r in f.resolved.items():
                c = ep_path(consumer_ep)
                p = ep_path(r["producer"])
                if c in live and p not in live:
                    live.add(p)
                    grew = True
        return live

    # ------------------------------------------------------------------ the tick

    def tick(self, observed: list[str] | None = None,
             journal: dict[str, Any] | None = None) -> dict:
        f = self.f
        observed = observed or []
        journal = journal or {}
        sigma: dict[str, str] = {}
        effects: list[Any] = []
        ran: list[str] = []
        pending: list[tuple[str, Any]] = []
        live = self._live_set(observed)

        def put(e: str, v: Any) -> None:
            sigma[e] = self.put_blob(v)

        def get(e: str):
            bid = sigma.get(e)
            return (None, False) if bid is None else (self.blobs[bid], True)

        def ctx_for(path: str, jv: Any = None, has_j: bool = False) -> Ctx:
            site = f.sites[path]
            addr = site["pane"]["mem"]
            return Ctx(
                powers=set(site["unit"]["powers"]),
                config=site["pane"]["config"],
                read_log=lambda: [self.blobs[b] for b in self.mem.get(addr, [])] if addr else [],
                append_log=lambda v: pending.append((addr, v)) if addr else None,
                journal_value=jv,
                has_journal=has_j,
                emit_world=effects.append,
            )

        def gather(path: str):
            site = f.sites[path]
            inp: dict[str, Any] = {}
            for p in site["unit"]["ports"]:
                if p["dir"] != "in":
                    continue
                r = f.resolved.get(ep(path, p["name"]))
                if r is None:
                    return None
                raw, ok = get(r["producer"])
                if not ok:
                    return None
                inp[p["name"]] = self.frames.apply(r["route"], raw)
            # all or nothing, so what lands in sigma never depends on port declaration order
            for k, v in inp.items():
                put(ep(path, k), v)
            return inp

        # pass 0 — sources. Novelty enters, journalled on the way in.
        for path, site in f.sites.items():
            if site["unit"]["cls"] != "source" or path not in live:
                continue
            d = self._prim(site)
            out_port = next(p for p in site["unit"]["ports"] if p["dir"] == "out")
            key = ep(path, out_port["name"])
            jv, has = (journal[key], True) if key in journal else (None, False)
            out = d["cook"]({}, ctx_for(path, jv, has))
            ran.append(path)
            for k, v in out.items():
                put(ep(path, k), v)

        # pass A — delay reads. Handed NO inputs; the signature has no parameter for them.
        for path, site in f.sites.items():
            if site["unit"]["cls"] != "delay" or path not in live:
                continue
            out = self._prim(site)["read"](ctx_for(path))
            ran.append(path)
            for k, v in out.items():
                put(ep(path, k), v)

        # pass B — the body, topological, memoised on content
        for path in f.order:
            site = f.sites[path]
            if path not in live:
                continue
            cls = site["unit"]["cls"]
            if cls not in ("pure", "effect"):
                continue
            inp = gather(path)
            if inp is None:
                continue

            if cls == "pure":
                cfg = site["pane"]["config"]
                key = h({
                    "@": "memo/1",
                    "unit": site["pane"]["unit"],
                    "config": None if cfg is None else blob_id(cfg),
                    "inputs": {k: blob_id(v) for k, v in inp.items()},
                })
                hit = self.memo.get(key)
                if hit is not None:
                    for k, bid in hit.items():
                        sigma[ep(path, k)] = bid
                else:
                    out = self._prim(site)["cook"](inp, ctx_for(path))
                    ran.append(path)
                    ids = {}
                    for k, v in out.items():
                        put(ep(path, k), v)
                        ids[k] = sigma[ep(path, k)]
                    self.memo[key] = ids
            else:
                sig = h({k: blob_id(v) for k, v in inp.items()})
                if self.last_effect_inputs.get(path) != sig:
                    self.last_effect_inputs[path] = sig
                    self._prim(site)["act"](inp, ctx_for(path))
                    ran.append(path)

        # pass C — delay writes. Inputs have settled; appends are buffered.
        for path, site in f.sites.items():
            if site["unit"]["cls"] != "delay" or path not in live:
                continue
            inp = gather(path)
            if inp is None:
                continue
            self._prim(site)["write"](inp, ctx_for(path))

        # pass D — commit. The logs grow by one tick's worth.
        for addr, v in pending:
            self.mem.setdefault(addr, []).append(self.put_blob(v))

        tick = self.tick_index
        self.tick_index += 1
        return {
            "tick": tick,
            "sigmaHash": h({"@": "sigma/1", "tick": float(tick), "at": sigma}),
            "sigma": sigma,
            "ran": sorted(ran),
            "effects": effects,
        }

    def _prim(self, site: dict) -> dict:
        b = site["unit"]["body"]
        if b["@"] != "prim/1":
            raise RuntimeError("host bodies are not executable in the oracle")
        d = PRIMS.get(b["op"])
        if d is None:
            raise RuntimeError(f"unknown primitive: {b['op']}")
        return d
