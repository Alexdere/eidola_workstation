"""The grader. Feed both cores the same workspace and the same source-emission log, replay,
assert sigma bytes equal at every tick.

Any divergence is a bug in a core — or an under-specified spot in the spec, and the second is
the more valuable find. Error *messages* are not observables and are not compared; whether a
workspace was refused is.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

from run import run_scenario

CORE = Path(__file__).resolve().parent.parent / "core"

# op -> (in ports with shapes, out port shape or None)
LEAVES = {
    "core.const":  ([], "any"),
    "core.add":    ([("a", "num"), ("b", "num")], "num"),
    "core.sub":    ([("a", "num"), ("b", "num")], "num"),
    "core.mul":    ([("a", "num"), ("b", "num")], "num"),
    "core.neg":    ([("a", "num")], "num"),
    "core.gt":     ([("a", "num"), ("b", "num")], "bool"),
    "core.eq":     ([("a", "any"), ("b", "any")], "bool"),
    "core.not":    ([("a", "bool")], "bool"),
    "core.and":    ([("a", "bool"), ("b", "bool")], "bool"),
    "core.or":     ([("a", "bool"), ("b", "bool")], "bool"),
    "core.if":     ([("c", "bool"), ("a", "any"), ("b", "any")], "any"),
    "core.concat": ([("a", "str"), ("b", "str")], "str"),
    "core.list2":  ([("a", "any"), ("b", "any")], "list"),
    "core.source": ([], "any"),
    "core.delay":  ([("x", "any")], "any"),
    "core.sum":    ([("x", "num")], "num"),
    "core.print":  ([("x", "any")], None),
    "core.lt":     ([("a", "num"), ("b", "num")], "bool"),
    "core.abs":    ([("a", "num")], "num"),
    "core.min":    ([("a", "num"), ("b", "num")], "num"),
    "core.max":    ([("a", "num"), ("b", "num")], "num"),
    "core.floor":  ([("a", "num")], "num"),
    "core.len":    ([("a", "list")], "num"),
    "core.at":     ([("a", "list"), ("i", "num")], "any"),
    "core.append": ([("a", "list"), ("x", "any")], "list"),
    "core.hist":   ([("x", "any")], "list"),
}
PURE = [k for k, (i, o) in LEAVES.items() if o and k not in ("core.source", "core.delay", "core.sum")]
DELAYS = ("core.delay", "core.sum", "core.hist")


def compatible(a: str, b: str) -> bool:
    return a == "any" or b == "any" or a == b


def rand_value(rng: random.Random, depth: int = 0):
    r = rng.random()
    if r < 0.45:
        return {"d": _f(rng.choice([0.0, 1.0, -1.0, 2.5, -0.0, 1e10, 1e-10, 3.0, 42.0]))}
    if r < 0.6:
        return {"b": rng.random() < 0.5}
    if r < 0.7:
        return {"s": rng.choice(["", "a", "hello", "é", "\U0001D400"])}
    if r < 0.78:
        return {"n": None}
    if r < 0.85:
        return {"x": bytes(rng.randrange(256) for _ in range(rng.randrange(4))).hex()}
    if depth < 2 and r < 0.93:
        return {"l": [rand_value(rng, depth + 1) for _ in range(rng.randrange(3))]}
    if depth < 2:
        return {"m": {k: rand_value(rng, depth + 1) for k in rng.sample("abcxyz", rng.randrange(3))}}
    return {"d": _f(0.0)}


def _f(x: float) -> str:
    import struct
    return struct.pack(">d", x).hex()


def make_container(rng: random.Random, name: str) -> dict:
    """A small composite: k in-ports, one out. Exercises @in/@out relays and nesting."""
    op = rng.choice(["core.add", "core.mul", "core.sub", "core.list2", "core.if"])
    ins, out = LEAVES[op]
    ports = [[f"i{n}", "in", sh, None] for n, (_p, sh) in enumerate(ins)]
    ports.append(["o", "out", out, None])
    wires = [[["@in", f"i{n}"], ["c", p]] for n, (p, _sh) in enumerate(ins)]
    wires.append([["c", "out"], ["@out", "o"]])
    return {"container": {"ports": ports, "graph": {"panes": {"c": {"unit": op}}, "wires": wires}}}


def gen_workspace(rng: random.Random) -> dict:
    units: dict[str, dict] = {op: {"prim": op} for op in LEAVES}

    # frame-carrying variants, so canonical route selection is exercised (c->k has two routes)
    units["kC"] = {"prim": "core.const", "frames": {"out": "temp.c"}}
    units["negF"] = {"prim": "core.neg", "frames": {"a": "temp.f"}}
    units["negK"] = {"prim": "core.neg", "frames": {"a": "temp.k"}}

    n_cont = rng.randrange(3)
    for i in range(n_cont):
        units[f"box{i}"] = make_container(rng, f"box{i}")

    panes: dict[str, dict] = {}
    wires: list = []
    # (occ, port, shape) available as producers, in creation order -> acyclic by construction
    produced: list[tuple[str, str, str]] = []
    entries: list[tuple[str, str, list, str | None]] = []

    n = rng.randrange(2, 9)
    for i in range(n):
        occ = f"p{i}"
        pick = rng.random()
        if pick < 0.08 and n_cont:
            uname = f"box{rng.randrange(n_cont)}"
            cont = units[uname]["container"]
            ins = [(p[0], p[2]) for p in cont["ports"] if p[1] == "in"]
            out = next((p[2] for p in cont["ports"] if p[1] == "out"), None)
            outname = "o"
        elif pick < 0.14:
            uname = rng.choice(["kC", "negF", "negK"])
            base = {"kC": "core.const", "negF": "core.neg", "negK": "core.neg"}[uname]
            ins, out = LEAVES[base]
            outname = "out"
        else:
            uname = rng.choice(list(LEAVES))
            ins, out = LEAVES[uname]
            outname = "out"

        pane: dict = {"unit": uname}
        if rng.random() < 0.5:
            pane["config"] = rand_value(rng)
        if uname in DELAYS:
            pane["mem"] = f"m{rng.randrange(3)}"     # sometimes shared: identity is content
        if rng.random() < 0.45:
            pane["pinned"] = True
        panes[occ] = pane
        entries.append((occ, uname, ins, outname if out else None))
        if out:
            produced.append((occ, outname, out))

    for idx, (occ, uname, ins, _out) in enumerate(entries):
        for pname, shape in ins:
            if rng.random() < 0.12:
                continue                              # unwired: absence is a member of the set
            # frame ports need a frame-carrying producer
            if uname in ("negF", "negK"):
                cands = [(o, p, s) for (o, p, s) in produced[:idx] if panes[o]["unit"] == "kC"]
            else:
                cands = [(o, p, s) for (o, p, s) in produced[:idx]
                         if compatible(s, shape) and panes[o]["unit"] != "kC"]
            if not cands:
                continue
            src = rng.choice(cands)
            wires.append([[src[0], src[1]], [occ, pname]])

    return {"@": "workspace/1", "units": units, "root": {"panes": panes, "wires": wires}}


def gen_scenario(rng: random.Random, i: int) -> dict:
    ws = gen_workspace(rng)
    sources = [occ for occ, p in ws["root"]["panes"].items() if p["unit"] == "core.source"]
    occs = list(ws["root"]["panes"])
    log = []
    for _ in range(rng.randrange(1, 6)):
        step: dict = {}
        if rng.random() < 0.7:
            step["observed"] = rng.sample(occs, rng.randrange(len(occs) + 1))
        j = {f"{s}#out": rand_value(rng) for s in sources if rng.random() < 0.8}
        if j:
            step["journal"] = j
        log.append(step)
    return {"name": f"fuzz-{i}", "workspace": ws, "log": log}


def run_core(scenarios: list[dict]) -> list[dict]:
    proc = subprocess.run(
        ["npx", "tsx", "tools/run.ts"], cwd=CORE,
        input=json.dumps({"scenarios": scenarios}), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"core runner failed:\n{proc.stderr[-3000:]}")
    return json.loads(proc.stdout)["results"]


def compare(a: dict, b: dict) -> list[str]:
    """a = oracle, b = core."""
    out = []
    if bool(a.get("error")) != bool(b.get("error")):
        return [f"{a['name']}: refusal differs — oracle {a.get('error')!r} / core {b.get('error')!r}"]
    if a.get("error"):
        return []
    if a["graphHash"] != b["graphHash"]:
        out.append(f"{a['name']}: graphHash {a['graphHash']} != {b['graphHash']}")
    if a["unitIds"] != b["unitIds"]:
        out.append(f"{a['name']}: unit ids differ")
    for i, (x, y) in enumerate(zip(a["ticks"], b["ticks"])):
        if x["sigmaHash"] != y["sigmaHash"]:
            keys = sorted(set(x["sigma"]) | set(y["sigma"]))
            d = [f"{k}: {x['sigma'].get(k, '-')[:12]} vs {y['sigma'].get(k, '-')[:12]}"
                 for k in keys if x["sigma"].get(k) != y["sigma"].get(k)]
            out.append(f"{a['name']} tick {i}: sigma differs — " + "; ".join(d[:6]))
        if x["ran"] != y["ran"]:
            out.append(f"{a['name']} tick {i}: ran {x['ran']} != {y['ran']}")
        if x["effects"] != y["effects"]:
            out.append(f"{a['name']} tick {i}: effects differ")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--show", type=int, default=12)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    scenarios = [gen_scenario(rng, i) for i in range(args.n)]
    oracle = [run_scenario(s) for s in scenarios]
    core = run_core(scenarios)

    bad: list[str] = []
    for a, b in zip(oracle, core):
        bad += compare(a, b)

    refused = sum(1 for r in oracle if r.get("error"))
    ticks = sum(len(r["ticks"]) for r in oracle)
    print(f"{args.n} workspaces, {ticks} ticks graded, {refused} refused by both")
    if bad:
        print(f"DIVERGENCE — {len(bad)}:")
        for b in bad[: args.show]:
            print("  -", b)
        failing = sorted({b.split(":")[0] for b in bad})
        Path("divergence.json").write_text(json.dumps(
            {"scenarios": [s for s in scenarios if s["name"] in failing]}, indent=2))
        print(f"  wrote {len(failing)} failing scenario(s) to divergence.json")
        return 1
    print("bit-identical sigma at every tick")
    return 0


if __name__ == "__main__":
    sys.exit(main())
