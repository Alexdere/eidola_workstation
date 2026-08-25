// The two holes the core defines and does not fill (core-spec.md §1).
//
// A port is a socket, not a choice. The browser / worker / daemon tiers become three plugs,
// not three products. If the core knew about IndexedDB, the daemon version would be a fork.

import { Value } from "./canon.js";
import { Body, Unit } from "./model.js";
import { Ctx } from "./caps.js";

/** "store and fetch immutable things by their content id." */
export interface PersistencePort {
  putBlob(id: string, v: Value): void;
  getBlob(id: string): Value | undefined;
  putUnit(id: string, u: Unit): void;
  getUnit(id: string): Unit | undefined;
  appendMem(addr: string, id: string): void;
  readMem(addr: string): string[];
  memAddrs(): string[];
  blobIds(): string[];
  unitIds(): string[];
}

export class MemoryPersistence implements PersistencePort {
  private blobs = new Map<string, Value>();
  private units = new Map<string, Unit>();
  private mem = new Map<string, string[]>();

  putBlob(id: string, v: Value) { if (!this.blobs.has(id)) this.blobs.set(id, v); }
  getBlob(id: string) { return this.blobs.get(id); }
  putUnit(id: string, u: Unit) { if (!this.units.has(id)) this.units.set(id, u); }
  getUnit(id: string) { return this.units.get(id); }
  appendMem(addr: string, id: string) {
    const l = this.mem.get(addr);
    if (l) l.push(id); else this.mem.set(addr, [id]);
  }
  readMem(addr: string) { return this.mem.get(addr) ?? []; }
  memAddrs() { return [...this.mem.keys()].sort(); }
  blobIds() { return [...this.blobs.keys()].sort(); }
  unitIds() { return [...this.units.keys()].sort(); }
}

/**
 * "run this unit with exactly these powers." Filled by local / worker / extension / daemon.
 *
 * The core knows a leaf's CLASS — pure, source, delay, effect — because class is about
 * novelty, ordering and what may be memoised. It does not know, and must not know, what a
 * leaf COMPUTES. A body is opaque here; the host is what understands it. Every vocabulary of
 * operations therefore lives on the far side of this port, where it can grow without the
 * kernel growing. That is the whole reason the port exists.
 */
export interface LeafDef {
  /** pure & source, pass B: inputs in, outputs out. A missing key means absent. */
  cook?(inp: Record<string, Value>, ctx: Ctx): Record<string, Value>;
  /** delay, pass A: takes NO inputs — it cannot see them. */
  read?(ctx: Ctx): Record<string, Value>;
  /** delay, pass C: inputs have settled; the append is buffered until pass D. */
  write?(inp: Record<string, Value>, ctx: Ctx): void;
  /** effect, pass B: no outputs, so nothing can observe it — it must be pinned to live. */
  act?(inp: Record<string, Value>, ctx: Ctx): void;
}

export interface ExecutionPort {
  readonly kind: string;
  /** make an opaque body callable, or throw if this host cannot run it */
  resolve(body: Body, unit: Unit): LeafDef;
}
