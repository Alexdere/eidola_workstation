// CORE/1 objects — the seam and what hangs off it. See spec/CORE-FORMAT.md §1.

import { Value, hash } from "./canon.js";

export type Dir = "in" | "out";
export type Cls = "pure" | "source" | "delay" | "effect" | "container";

export interface Port {
  "@": "port/1";
  name: string;
  dir: Dir;
  shape: string;
  frame: string | null;
}

export type Body =
  | { "@": "prim/1"; op: string }
  | { "@": "host/1"; lang: string; src: string };

export interface Unit {
  "@": "unit/1";
  ports: Port[];      // ordered — a boundary is an ordered interface
  powers: string[];   // sorted — a set has no order to change
  cls: Cls;
  body: Body | null;  // exactly one of body / graph
  graph: Graph | null;
}

export interface Pane {
  "@": "pane/1";
  unit: string;            // "unit:<hex>"
  config: Value | null;    // held inline; its blob id is what enters identity
  mem: string | null;
  pinned: boolean;
}

export interface Wire {
  "@": "wire/1";
  from: [string, string];
  to: [string, string];
}

export interface Graph {
  "@": "graph/1";
  panes: Record<string, Pane>;
  wires: Wire[];
}

export const IN = "@in";
export const OUT = "@out";
export const OCC_RE = /^[A-Za-z0-9_.-]+$/;

// ---------------------------------------------------------------- builders

export function port(
  name: string, dir: Dir, shape: string, frame: string | null = null,
): Port {
  return { "@": "port/1", name, dir, shape, frame };
}

export function leaf(
  cls: Exclude<Cls, "container">, body: Body, ports: Port[], powers: string[] = [],
): Unit {
  return {
    "@": "unit/1",
    ports,
    powers: [...powers].sort(),
    cls,
    body,
    graph: null,
  };
}

export function container(ports: Port[], graph: Graph): Unit {
  // Law 4: no composite may bind a power. Not "should not" — the builder has no parameter
  // for one, so the violation is unwritable here.
  return { "@": "unit/1", ports, powers: [], cls: "container", body: null, graph };
}

export function pane(
  unitId: string,
  opts: { config?: Value; mem?: string; pinned?: boolean } = {},
): Pane {
  return {
    "@": "pane/1",
    unit: unitId,
    config: opts.config === undefined ? null : opts.config,
    mem: opts.mem ?? null,
    pinned: opts.pinned ?? false,
  };
}

export function wire(from: [string, string], to: [string, string]): Wire {
  return { "@": "wire/1", from, to };
}

export function graph(panes: Record<string, Pane>, wires: Wire[]): Graph {
  return { "@": "graph/1", panes, wires };
}

// ---------------------------------------------------------------- identity

/**
 * Wires are sorted before hashing so authoring order cannot enter identity (Law 8);
 * ports are not, because a boundary is an ordered interface. The asymmetry is deliberate
 * and is the boundary case a byte-identical test exists to catch.
 */
export function canonicalGraph(g: Graph): Value {
  const wires = [...g.wires]
    .map((w) => ({ "@": w["@"], from: [w.from[0], w.from[1]], to: [w.to[0], w.to[1]] }))
    .sort((a, b) => (hash(a as Value) < hash(b as Value) ? -1 : 1));
  const panes: Record<string, Value> = {};
  for (const k of Object.keys(g.panes)) panes[k] = g.panes[k] as unknown as Value;
  return { "@": "graph/1", panes, wires: wires as unknown as Value };
}

export function canonicalUnit(u: Unit): Value {
  return {
    "@": "unit/1",
    ports: u.ports as unknown as Value,
    powers: u.powers,
    cls: u.cls,
    body: (u.body ?? null) as unknown as Value,
    graph: u.graph ? canonicalGraph(u.graph) : null,
  };
}

export function unitId(u: Unit): string {
  return "unit:" + hash(canonicalUnit(u));
}

export function blobId(v: Value): string {
  return "blob:" + hash(v);
}

export function graphHash(g: Graph): string {
  return hash(canonicalGraph(g));
}

/** The Law 8 observable: presentation moves must not change this string. */
export function serializeGraph(g: Graph): string {
  return graphHash(g);
}

// ---------------------------------------------------------------- helpers

export function findPort(u: Unit, name: string): Port | undefined {
  return u.ports.find((p) => p.name === name);
}

export function inPorts(u: Unit): Port[] {
  return u.ports.filter((p) => p.dir === "in");
}

export function outPorts(u: Unit): Port[] {
  return u.ports.filter((p) => p.dir === "out");
}
