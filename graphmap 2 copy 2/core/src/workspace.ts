// WORKSPACE/1 — the file. Versioned, documented, and readable by things that are not this
// app. This is requirement #1 from the merge order, and it arrives first because the oracle
// forces it: two cores can only be graded against each other over a format neither owns.

import { Value } from "./canon.js";
import { Cls, Graph, Pane, Port, Unit, container, graph, leaf, pane, port, wire } from "./model.js";
import { primUnit } from "./prims.js";
import { Store } from "./store.js";
import { VSpec, fromSpec, toSpec } from "./vspec.js";

export type PortSpec = [string, "in" | "out", string, string | null];

export type UnitSpec =
  | { prim: string; frames?: Record<string, string> }
  /** an authored leaf: its own boundary, its own powers, its own body. The normal case. */
  | { leaf: { cls: string; ports: PortSpec[]; powers: string[]; lang: string; src: string } }
  | { container: { ports: PortSpec[]; graph: GraphSpec } }
  /** a unit with no identity yet. It travels, but it travels as unfinished work. */
  | { draft: { ports: PortSpec[]; graph: GraphSpec } };

export interface PaneSpec {
  unit: string;                 // a local name in `units`
  config?: VSpec | null;
  mem?: string | null;
  pinned?: boolean;
}

export interface GraphSpec {
  panes: Record<string, PaneSpec>;
  wires: Array<[[string, string], [string, string]]>;
}

export interface WorkspaceSpec {
  "@": "workspace/1";
  units: Record<string, UnitSpec>;
  root: GraphSpec;
}

export function loadWorkspace(ws: WorkspaceSpec, store: Store): {
  root: Graph;
  unitIds: Record<string, string>;
} {
  const ids: Record<string, string> = {};

  const buildUnit = (name: string, seen: Set<string>): string => {
    if (ids[name]) return ids[name];
    if (seen.has(name)) throw new Error(`unit cycle at ${name}`);
    seen.add(name);
    const s = ws.units[name];
    if (!s) throw new Error(`unknown unit name: ${name}`);
    let u: Unit;
    if ("prim" in s) {
      const base = primUnit(s.prim);
      u = s.frames
        ? {
            ...base,
            ports: base.ports.map((p) =>
              s.frames && p.name in s.frames ? ({ ...p, frame: s.frames[p.name] } as Port) : p,
            ),
          }
        : base;
    } else if ("draft" in s) {
      const ports = s.draft.ports.map(([n, d, sh, fr]) => port(n, d, sh, fr));
      const id = store.putDraft(container(ports, buildGraph(s.draft.graph, seen)));
      ids[name] = id; seen.delete(name); return id;
    } else if ("leaf" in s) {
      const L = s.leaf;
      u = leaf(
        L.cls as Exclude<Cls, "container">,
        { "@": "host/1", lang: L.lang, src: L.src },
        L.ports.map(([n, d, sh, fr]) => port(n, d, sh, fr)),
        L.powers,
      );
    } else {
      const ports = s.container.ports.map(([n, d, sh, fr]) => port(n, d, sh, fr));
      u = container(ports, buildGraph(s.container.graph, seen));
    }
    const id = store.putUnit(u);
    ids[name] = id;
    seen.delete(name);
    return id;
  };

  const buildGraph = (gs: GraphSpec, seen: Set<string>): Graph => {
    const panes: Record<string, Pane> = {};
    for (const occ of Object.keys(gs.panes)) {
      const ps = gs.panes[occ];
      panes[occ] = pane(buildUnit(ps.unit, seen), {
        config: ps.config == null ? undefined : (fromSpec(ps.config) as Value),
        mem: ps.mem ?? undefined,
        pinned: ps.pinned ?? false,
      });
    }
    return graph(panes, gs.wires.map(([a, b]) => wire(a, b)));
  };

  const root = buildGraph(ws.root, new Set());
  for (const name of Object.keys(ws.units)) buildUnit(name, new Set());
  return { root, unitIds: ids };
}

/**
 * The inverse: a live graph back to a WORKSPACE/1 file. Unit ids are used as their own local
 * names, which is honest — a content address is the most stable name a unit can have.
 * This is what makes the workspace "readable by things that aren't your app".
 */
export function dumpWorkspace(store: Store, root: Graph): WorkspaceSpec {
  const units: Record<string, UnitSpec> = {};

  const emit = (id: string): void => {
    if (units[id]) return;
    const u = store.unit(id);
    if (Store.isDraft(id)) {
      units[id] = {
        draft: {
          ports: u.ports.map((p) => [p.name, p.dir, p.shape, p.frame] as PortSpec),
          graph: dumpGraph(u.graph ?? graph({}, [])),
        },
      };
    } else if (u.graph) {
      units[id] = {
        container: {
          ports: u.ports.map((p) => [p.name, p.dir, p.shape, p.frame] as PortSpec),
          graph: dumpGraph(u.graph),
        },
      };
    } else if (u.body && u.body["@"] === "prim/1") {
      const frames: Record<string, string> = {};
      for (const p of u.ports) if (p.frame) frames[p.name] = p.frame;
      units[id] = Object.keys(frames).length
        ? { prim: u.body.op, frames }
        : { prim: u.body.op };
    } else if (u.body && u.body["@"] === "host/1") {
      // an authored leaf travels whole: shipping the form ships the program
      units[id] = {
        leaf: {
          cls: u.cls,
          ports: u.ports.map((p) => [p.name, p.dir, p.shape, p.frame] as PortSpec),
          powers: [...u.powers],
          lang: u.body.lang,
          src: u.body.src,
        },
      };
    } else {
      throw new Error("a unit with no body and no graph cannot be dumped");
    }
  };

  const dumpGraph = (g: Graph): GraphSpec => {
    const panes: Record<string, PaneSpec> = {};
    for (const occ of Object.keys(g.panes)) {
      const p = g.panes[occ];
      emit(p.unit);
      panes[occ] = {
        unit: p.unit,
        config: p.config === null ? null : toSpec(p.config),
        mem: p.mem,
        pinned: p.pinned,
      };
    }
    return {
      panes,
      wires: g.wires.map((w) => [w.from, w.to] as [[string, string], [string, string]]),
    };
  };

  const rootSpec = dumpGraph(root);
  return { "@": "workspace/1", units, root: rootSpec };
}
