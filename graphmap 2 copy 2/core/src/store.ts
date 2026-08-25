// G and B — the two immutable, content-addressed lifetimes.
//
// Nothing here is ever mutated. The only thing that changes is which immutable objects are
// currently referenced, which is why replay, snapshot, undo and migration are the same
// three-line operation rather than four features.

import { Value } from "./canon.js";
import { Unit, blobId, unitId } from "./model.js";
import { MemoryPersistence, PersistencePort } from "./ports.js";

export class Store {
  /**
   * Lineage: which unit this one was melted from. It lives BESIDE the store, never inside a
   * unit — because a unit's identity is its content, and two structurally identical units
   * must remain the same thing however each of them came to be. Putting `parent_hash` in the
   * content would make identical things distinct, which is Law 3 backwards.
   */
  readonly lineage = new Map<string, string>();

  constructor(readonly persist: PersistencePort = new MemoryPersistence()) {}

  recordLineage(child: string, parent: string): void {
    if (child !== parent) this.lineage.set(child, parent);
  }

  parentOf(id: string): string | undefined {
    return this.lineage.get(id);
  }

  ancestry(id: string): string[] {
    const out: string[] = [];
    let cur = this.lineage.get(id);
    while (cur && !out.includes(cur)) { out.push(cur); cur = this.lineage.get(cur); }
    return out;
  }

  putUnit(u: Unit): string {
    const id = unitId(u);
    this.persist.putUnit(id, u);
    // Merkle by construction: a container's interior references its children by id, so
    // storing the parent stores nothing about the children that is not already stored.
    if (u.graph) for (const p of Object.values(u.graph.panes)) void p.unit;
    return id;
  }

  // ---------------------------------------------------------------- drafts
  /**
   * A DRAFT is a unit that exists and has no identity. Its id is a local ticket, not a
   * content address — which is exactly the claim being made: this thing is not yet citable,
   * not yet shareable, not yet memoised, and nothing anywhere may depend on it being what it
   * is right now. A1 is untouched, because a draft id never asserts that content named it.
   *
   * A graph holding a draft is therefore itself provisional, all the way up. Declaring seals
   * the interior bottom-up and hands back one hash for the whole thing.
   */
  private drafts = new Map<string, Unit>();
  private draftN = 0;

  static isDraft(id: string): boolean { return id.startsWith("draft:"); }

  putDraft(u: Unit): string {
    const id = `draft:${++this.draftN}`;
    this.drafts.set(id, u);
    return id;
  }
  /** replace a draft's content in place — it has no identity to disturb */
  setDraft(id: string, u: Unit): void {
    if (!this.drafts.has(id)) throw new Error(`unknown draft: ${id}`);
    this.drafts.set(id, u);
  }
  draftIds(): string[] { return [...this.drafts.keys()]; }

  /** does anything reachable from here still lack an identity? */
  hasDraft(u: Unit): boolean {
    if (!u.graph) return false;
    for (const p of Object.values(u.graph.panes)) {
      if (Store.isDraft(p.unit)) return true;
      if (this.hasDraft(this.unit(p.unit))) return true;
    }
    return false;
  }

  /**
   * DECLARE. Seal a draft: every draft inside it is declared first (Merkle leaves no choice),
   * then the container itself is hashed. What was a loose interior becomes one thing with one
   * name — a chip. Returns the new content id.
   */
  declare(id: string): string {
    if (!Store.isDraft(id)) return id;
    const u = this.drafts.get(id);
    if (!u) throw new Error(`unknown draft: ${id}`);
    let sealed = u;
    if (u.graph) {
      const panes: Record<string, import("./model.js").Pane> = {};
      for (const [occ, p] of Object.entries(u.graph.panes))
        panes[occ] = Store.isDraft(p.unit) ? { ...p, unit: this.declare(p.unit) } : p;
      sealed = { ...u, graph: { ...u.graph, panes } };
    }
    const real = this.putUnit(sealed);
    this.drafts.delete(id);
    return real;
  }

  unit(id: string): Unit {
    const d = this.drafts.get(id);
    if (d) return d;
    const u = this.persist.getUnit(id);
    if (!u) throw new Error(`unknown unit: ${id}`);
    return u;
  }

  hasUnit(id: string): boolean {
    return this.drafts.has(id) || this.persist.getUnit(id) !== undefined;
  }

  putBlob(v: Value): string {
    const id = blobId(v);
    this.persist.putBlob(id, v);
    return id;
  }

  blob(id: string): Value {
    const v = this.persist.getBlob(id);
    if (v === undefined) throw new Error(`unknown blob: ${id}`);
    return v;
  }

  /** Copy G and B (and memory) into another store. The memo table is deliberately not
   *  shipped: it is cache tier and rebuilds deterministically. If anything lived outside
   *  (G, B, σ, M) migration would lose it, and the test would say so. */
  copyInto(dest: Store): void {
    for (const id of this.persist.unitIds()) dest.persist.putUnit(id, this.persist.getUnit(id)!);
    for (const id of this.persist.blobIds()) dest.persist.putBlob(id, this.persist.getBlob(id)!);
    for (const a of this.persist.memAddrs())
      for (const id of this.persist.readMem(a)) dest.persist.appendMem(a, id);
    for (const [c, p] of this.lineage) dest.lineage.set(c, p);
  }
}
