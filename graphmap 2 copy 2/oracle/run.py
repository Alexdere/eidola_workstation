"""Run scenarios through the oracle, reporting exactly what the spec says is observable."""

from __future__ import annotations

import json
import sys

from graphmap_oracle import Engine, graph_hash, load_workspace
from graphmap_oracle.workspace import from_spec, to_spec


def run_scenario(s: dict) -> dict:
    try:
        root, units, ids = load_workspace(s["workspace"])
        e = Engine(root, units)
        ticks = []
        for step in s["log"]:
            j = {k: from_spec(v) for k, v in (step.get("journal") or {}).items()}
            r = e.tick(observed=step.get("observed"), journal=j)
            ticks.append({
                "tick": r["tick"],
                "sigmaHash": r["sigmaHash"],
                "sigma": r["sigma"],
                "ran": r["ran"],
                "effects": [to_spec(v) for v in r["effects"]],
            })
        return {"name": s["name"], "graphHash": graph_hash(root), "unitIds": ids, "ticks": ticks}
    except Exception as err:  # a refusal is an observable too
        return {"name": s["name"], "graphHash": "", "unitIds": {}, "ticks": [],
                "error": str(err)}


def main() -> None:
    data = json.load(sys.stdin)
    json.dump({"results": [run_scenario(s) for s in data["scenarios"]]}, sys.stdout)


if __name__ == "__main__":
    main()
