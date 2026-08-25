// Liminal space — `claude/the-head.md` §4.
//
// Content addressing has a real cost: immutability makes editing awkward, because every
// keystroke is a new hash. Liminal space is the answer — mutation is quarantined OUTSIDE the
// addressed world. Nothing is hashed while you compose, because nothing is addressed yet.
//
//   | stage    | mutable | addressed | citable | evaluable        |
//   | liminal  | yes     | no        | no      | yes, unmemoised  |
//   | declared | no      | yes       | yes     | yes, memoised    |
//
// Two transitions: MELT takes a declared unit into liminal space (the original is untouched
// because it cannot be touched); DECLARE freezes the boundary, at which point the hash exists.
// The working tree is liminal; the commit is the declaration; the hash exists only afterward.

import { Value } from "./canon.js";
import {
  Graph, IN, OUT, Pane, Port, Unit, container, findPort, graph, pane, port, wire, Wire,
} from "./model.js";
import { Store } from "./store.js";
import { MemoryPersistence, PersistencePort } from "./ports.js";
import { Engine, TickInput, TickResult } from "./engine.js";
import { FrameRegistry } from "./frames.js";
import { flatten } from "./flatten.js";

/**
 * A store that READS THROUGH to a parent and writes only locally. This is what makes liminal
 * space cheap: a draft can be evaluated as many times as you like and the real store never
 * learns about any of the intermediate versions.
 */
export class ScratchPersistence implements PersistencePort {
  private local = new MemoryPersistence();
  constructor(private parent: PersistencePort) {}
  putBlob(id: string, v: Value) { this.local.putBlob(id, v); }
  getBlob(id: string) { return this.local.getBlob(id) ?? this.parent.getBlob(id); }
  putUnit(id: string, u: Unit) { this.local.putUnit(id, u); }
  getUnit(id: string) { return this.local.getUnit(id) ?? this.parent.getUnit(id); }
  appendMem(addr: string, id: string) { this.local.appendMem(addr, id); }
  readMem(addr: string) {
    const l = this.local.readMem(addr);
    return l.length ? l : this.parent.readMem(addr);
  }
  memAddrs() { return [...new Set([...this.parent.memAddrs(), ...this.local.memAddrs()])].sort(); }
  blobIds() { return this.local.blobIds(); }
  unitIds() { return this.local.unitIds(); }
}

const sanitise = (s: string) => s.replace(/[^A-Za-z0-9_.-]/g, "_");

export interface Crossing {
  dir: "in" | "out";
  /** the boundary port this crossing becomes */
  boundary: Port;
  /** the endpoint outside the selection */
  external: [string, string];
  /** the endpoint inside the selection */
  internal: [string, string];
}

/**
 * A draft. Mutable, unaddressed, uncitable — there is no name for it, so nothing can
 * reference it, so nothing can be broken by changing it.
 */
export class Liminal {
  constructor(
    public ports: Port[],
    public graph: Graph,
    /** the unit this was melted from, if any. Lineage, NOT identity — see recordLineage. */
    public meltedFrom: string | null = null,
    public crossings: Crossing[] = [],
  ) {}

  addPane(occ: string, p: Pane): void { this.graph.panes[occ] = p; }
  removePane(occ: string): void {
    delete this.graph.panes[occ];
    this.graph.wires = this.graph.wires.filter((w) => w.from[0] !== occ && w.to[0] !== occ);
  }
  addWire(w: Wire): void { this.graph.wires.push(w); }
  addPort(p: Port): void { this.ports.push(p); }

  /**
   * Playable, not citable. Evaluated in a scratch store with the memo OFF, against the
   * TENTATIVE boundary: unwired boundary inputs are simply absent unless a stub is supplied.
   * Required, or Closure is unusable — you must hear it while writing it.
   */
  play(
    store: Store, stubs: Record<string, Value> = {}, input: TickInput = {},
    frames?: FrameRegistry,
  ): { result: TickResult; scratch: Store } {
    const scratch = new Store(new ScratchPersistence(store.persist));
    const panes: Record<string, Pane> = { ...this.graph.panes };
    const wires: Wire[] = [];
    const konst = scratch.putUnit(
      { "@": "unit/1", ports: [port("out", "out", "any")], powers: [], cls: "pure",
        body: { "@": "prim/1", op: "core.const" }, graph: null },
    );

    for (const w of this.graph.wires) {
      if (w.from[0] === IN) {
        const name = w.from[1];
        if (!(name in stubs)) continue;                  // absent, which is a legal state
        const occ = `_stub_${sanitise(name)}`;
        panes[occ] = pane(konst, { config: stubs[name] });
        wires.push(wire([occ, "out"], w.to));
        continue;
      }
      if (w.to[0] === OUT) continue;                     // the value is already in sigma
      wires.push(w);
    }

    const e = new Engine(scratch, graph(panes, wires), frames, undefined, { memo: false });
    return { result: e.tick(input), scratch };
  }

  /**
   * Freeze the boundary. The hash exists only now — this is the one event where lineage
   * belongs, and it is an event the author consciously performs.
   */
  declare(store: Store, frames?: FrameRegistry): string {
    const u = container(this.ports, this.graph);
    // refuse a malformed composite HERE rather than at the first tick that touches it
    const probe = new Store(new ScratchPersistence(store.persist));
    const id = probe.putUnit(u);
    flatten(graph({ probe: pane(id) }, []), probe, frames ?? new FrameRegistry());
    const real = store.putUnit(u);
    if (this.meltedFrom && this.meltedFrom !== real) store.recordLineage(real, this.meltedFrom);
    return real;
  }
}

/** Take a declared unit into liminal space. The original is untouched because it cannot be
 *  touched: what comes back is a copy with no identity. */
export function melt(store: Store, unitId: string): Liminal {
  const u = store.unit(unitId);
  if (!u.graph) throw new Error("only a composite can be melted");
  return new Liminal(
    u.ports.map((p) => ({ ...p })),
    { "@": "graph/1", panes: { ...u.graph.panes }, wires: [...u.graph.wires] },
    unitId,
  );
}

/**
 * Take a SELECTION out of a graph and infer its boundary from what crosses it.
 *
 * This is the closure gesture, and the boundary is not invented: it is exactly the set of
 * wires that already crossed the line you drew. "A graph you are building is a workspace
 * until you declare its exposure" — and the exposure is read off the crossings.
 */
export function meltSelection(g: Graph, occs: string[], store: Store): Liminal {
  const sel = new Set(occs);
  for (const occ of occs) if (!(occ in g.panes)) throw new Error(`no pane ${occ}`);

  const panes: Record<string, Pane> = {};
  for (const occ of occs) panes[occ] = g.panes[occ];

  const wires: Wire[] = [];
  const crossings: Crossing[] = [];
  const inPorts = new Map<string, Port>();
  const outPorts = new Map<string, Port>();

  const portOf = (occ: string, name: string): Port => {
    const p = findPort(store.unit(g.panes[occ].unit), name);
    if (!p) throw new Error(`no port ${occ}.${name}`);
    return p;
  };

  for (const w of g.wires) {
    const fromIn = sel.has(w.from[0]);
    const toIn = sel.has(w.to[0]);
    if (fromIn && toIn) { wires.push(w); continue; }
    if (!fromIn && !toIn) continue;

    if (!fromIn && toIn) {
      // an incoming crossing becomes a boundary IN port, shaped by its consumer
      const name = sanitise(`${w.to[0]}_${w.to[1]}`);
      const p = { ...portOf(w.to[0], w.to[1]), name, dir: "in" as const };
      inPorts.set(name, p);
      wires.push(wire([IN, name], w.to));
      crossings.push({ dir: "in", boundary: p, external: w.from, internal: w.to });
    } else {
      // an outgoing crossing becomes a boundary OUT port, shaped by its producer. Several
      // external consumers of one internal port share ONE boundary port: fan-out, not fan-in.
      const name = sanitise(`${w.from[0]}_${w.from[1]}`);
      const p = { ...portOf(w.from[0], w.from[1]), name, dir: "out" as const };
      if (!outPorts.has(name)) {
        outPorts.set(name, p);
        wires.push(wire(w.from, [OUT, name]));
      }
      crossings.push({ dir: "out", boundary: p, external: w.to, internal: w.from });
    }
  }

  const ports = [
    ...[...inPorts.values()].sort((a, b) => (a.name < b.name ? -1 : 1)),
    ...[...outPorts.values()].sort((a, b) => (a.name < b.name ? -1 : 1)),
  ];
  return new Liminal(ports, graph(panes, wires), null, crossings);
}

/**
 * The whole gesture: select, melt, declare, splice. What was several panes becomes one pane
 * bound to a new unit — usable anywhere any unit is usable, with no new construct. Law 2, as
 * something you can do with a mouse.
 */
export function collapse(
  g: Graph, occs: string[], store: Store, newOcc: string, frames?: FrameRegistry,
): { graph: Graph; unit: string; liminal: Liminal } {
  const lim = meltSelection(g, occs, store);
  const unit = lim.declare(store, frames);
  const sel = new Set(occs);

  const panes: Record<string, Pane> = {};
  for (const occ of Object.keys(g.panes)) if (!sel.has(occ)) panes[occ] = g.panes[occ];
  panes[newOcc] = pane(unit);

  const wires: Wire[] = [];
  for (const w of g.wires) {
    const fromIn = sel.has(w.from[0]);
    const toIn = sel.has(w.to[0]);
    if (fromIn && toIn) continue;                       // now interior
    if (!fromIn && !toIn) { wires.push(w); continue; }
    if (!fromIn && toIn) {
      wires.push(wire(w.from, [newOcc, sanitise(`${w.to[0]}_${w.to[1]}`)]));
    } else {
      wires.push(wire([newOcc, sanitise(`${w.from[0]}_${w.from[1]}`)], w.to));
    }
  }
  return { graph: graph(panes, wires), unit, liminal: lim };
}
