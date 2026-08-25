// The tick, and the command/event surface the head talks to.
//
// The five passes are not a rule ABOUT the runtime; they are its shape. And the five classes
// are the five passes — source/delay-read/body/delay-write/commit — which is the whole
// content of the taxonomy.

import { Value, hash } from "./canon.js";
import { Graph, Pane, Wire, blobId, graphHash } from "./model.js";
import { Store } from "./store.js";
import { Flat, epPath, epPortName, ep, flatten } from "./flatten.js";
import { FrameRegistry, defaultFrames } from "./frames.js";
import { makeCtx } from "./caps.js";
import { ExecutionPort, LeafDef, MemoryPersistence } from "./ports.js";
import { defaultHost } from "./defaulthost.js";

export interface TickInput {
  /** paths a head is looking at. Ephemeral — never content (Law 8). */
  observed?: string[];
  /** EP -> value. The enumerable openings, journalled on the way in (Law 5). */
  journal?: Record<string, Value>;
}

export interface TickResult {
  tick: number;
  sigma: Record<string, string>;
  sigmaHash: string;
  effects: Value[];
  /** paths that actually executed, as opposed to hitting the memo. The observable the
   *  two-panes-one-node claim is made of. */
  ran: string[];
}

export type Command =
  | { t: "add-pane"; occ: string; pane: Pane }
  | { t: "remove-pane"; occ: string }
  | { t: "add-wire"; wire: Wire }
  | { t: "remove-wire"; wire: Wire }
  | { t: "set-config"; occ: string; config: Value | null }
  | { t: "set-pinned"; occ: string; pinned: boolean };

export type Event =
  | { t: "graph-changed"; hash: string }
  | { t: "rejected"; reason: string };

export class Engine {
  private memo = new Map<string, Record<string, string>>(); // cache tier; never shipped
  private lastEffectInputs = new Map<string, string>();
  private flat: Flat | null = null;
  tickIndex = 0;

  /** memo off is what makes a liminal draft playable without filling the memo with the
   *  keys of two hundred versions you were only passing through. */
  private memoOn: boolean;

  constructor(
    public store: Store,
    public root: Graph,
    public frames: FrameRegistry = defaultFrames(),
    public host: ExecutionPort = defaultHost(),
    opts: { memo?: boolean } = {},
  ) {
    this.memoOn = opts.memo !== false;
    this.validate();
  }

  // ---------------------------------------------------------------- authoring

  /** Commands in, events out. No DOM, no screen — this is the whole head-facing surface. */
  apply(c: Command): Event {
    const before = this.root;
    const next: Graph = {
      "@": "graph/1",
      panes: { ...this.root.panes },
      wires: [...this.root.wires],
    };
    switch (c.t) {
      case "add-pane": next.panes[c.occ] = c.pane; break;
      case "remove-pane": {
        delete next.panes[c.occ];
        next.wires = next.wires.filter((w) => w.from[0] !== c.occ && w.to[0] !== c.occ);
        break;
      }
      case "add-wire": next.wires.push(c.wire); break;
      case "remove-wire":
        next.wires = next.wires.filter((w) => hash(w as unknown as Value) !== hash(c.wire as unknown as Value));
        break;
      case "set-config": {
        const p = next.panes[c.occ];
        if (!p) return { t: "rejected", reason: `no pane ${c.occ}` };
        next.panes[c.occ] = { ...p, config: c.config };
        break;
      }
      case "set-pinned": {
        const p = next.panes[c.occ];
        if (!p) return { t: "rejected", reason: `no pane ${c.occ}` };
        next.panes[c.occ] = { ...p, pinned: c.pinned };
        break;
      }
    }
    this.root = next;
    try {
      this.validate();
    } catch (e) {
      this.root = before;
      return { t: "rejected", reason: (e as Error).message };
    }
    return { t: "graph-changed", hash: this.graphHash() };
  }

  validate(): void {
    this.flat = flatten(this.root, this.store, this.frames);
  }

  private get f(): Flat {
    if (!this.flat) this.validate();
    return this.flat!;
  }

  /** The Law 8 observable. Gauge operations must leave this string alone. */
  graphHash(): string {
    return graphHash(this.root);
  }

  // ---------------------------------------------------------------- the tick

  tick(input: TickInput = {}): TickResult {
    const f = this.f;
    const journal = input.journal ?? {};
    const observed = input.observed ?? [];
    const sigma: Record<string, string> = {};
    const effects: Value[] = [];
    const ran: string[] = [];
    const pendingAppends: Array<[string, Value]> = [];

    const live = this.liveSet(observed);

    const put = (e: string, v: Value) => { sigma[e] = this.store.putBlob(v); };
    const get = (e: string): Value | undefined => {
      const id = sigma[e];
      return id === undefined ? undefined : this.store.blob(id);
    };

    const ctxFor = (path: string, journalValue?: Value) => {
      const site = f.sites.get(path)!;
      const addr = site.pane.mem;
      return makeCtx({
        powers: new Set(site.unit.powers),
        config: site.pane.config,
        readLog: () => (addr ? this.store.persist.readMem(addr).map((id) => this.store.blob(id)) : []),
        appendLog: (v) => { if (addr) pendingAppends.push([addr, v]); },
        journalValue,
        emitWorld: (v) => effects.push(v),
      });
    };

    /**
     * Gather a site's inputs, applying any frame conversions the wire declared.
     *
     * ABSENCE IS A MEMBER OF THE SET. An unwired port, or one whose upstream produced
     * nothing this tick, simply does not appear in the map — the body sees a missing key and
     * decides what that means. The core does not get to declare an input mandatory: how many
     * ports a unit has, and which of them matter, is the author's business.
     *
     * Nothing here is order-dependent: the gathered map is keyed by port name and sigma is
     * canonicalised with sorted keys, so what lands never depends on declaration order.
     */
    const gather = (path: string): Record<string, Value> => {
      const site = f.sites.get(path)!;
      const inp: Record<string, Value> = {};
      for (const p of site.unit.ports) {
        if (p.dir !== "in") continue;
        const r = f.resolved.get(ep(path, p.name));
        if (!r) continue;                          // unwired: absent
        const raw = get(r.producer);
        if (raw === undefined) continue;           // upstream produced nothing: absent
        inp[p.name] = this.frames.apply(r.route, raw);
      }
      for (const [k, v] of Object.entries(inp)) put(ep(path, k), v);
      return inp;
    };

    // --- pass 0: sources. Novelty enters, journalled on the way in.
    for (const site of f.sites.values()) {
      if (site.unit.cls !== "source" || !live.has(site.path)) continue;
      const def = this.prim(site);
      const outPort = site.unit.ports.find((p) => p.dir === "out")!;
      const jv = journal[ep(site.path, outPort.name)];
      const out = def.cook!({}, ctxFor(site.path, jv));
      ran.push(site.path);
      for (const [k, v] of Object.entries(out)) put(ep(site.path, k), v);
    }

    // --- pass A: delay reads. Handed NO inputs — the signature has no parameter for them.
    for (const site of f.sites.values()) {
      if (site.unit.cls !== "delay" || !live.has(site.path)) continue;
      const out = this.prim(site).read!(ctxFor(site.path));
      ran.push(site.path);
      for (const [k, v] of Object.entries(out)) put(ep(site.path, k), v);
    }

    // --- pass B: the body, topological, memoised on content
    for (const path of f.order) {
      const site = f.sites.get(path)!;
      if (!live.has(path)) continue;
      if (site.unit.cls !== "pure" && site.unit.cls !== "effect") continue;

      const inp = gather(path);

      if (site.unit.cls === "pure") {
        const key = hash({
          "@": "memo/1",
          unit: site.pane.unit,
          config: site.pane.config === null ? null : blobId(site.pane.config),
          inputs: Object.fromEntries(Object.entries(inp).map(([k, v]) => [k, blobId(v)])),
        });
        const hit = this.memoOn ? this.memo.get(key) : undefined;
        if (hit) {
          for (const [k, id] of Object.entries(hit)) sigma[ep(path, k)] = id;
        } else {
          const out = this.prim(site).cook!(inp, ctxFor(path));
          ran.push(path);
          const ids: Record<string, string> = {};
          for (const [k, v] of Object.entries(out)) {
            put(ep(path, k), v);
            ids[k] = sigma[ep(path, k)];
          }
          if (this.memoOn) this.memo.set(key, ids);
        }
      } else {
        // an effect runs only when its inputs changed: a world-facing act is not repeated
        // just because a tick happened
        const sig = hash(Object.fromEntries(
          Object.entries(inp).map(([k, v]) => [k, blobId(v)]),
        ));
        if (this.lastEffectInputs.get(path) !== sig) {
          this.lastEffectInputs.set(path, sig);
          this.prim(site).act!(inp, ctxFor(path));
          ran.push(path);
        }
      }
    }

    // --- pass C: delay writes. Inputs have settled; appends are buffered.
    for (const site of f.sites.values()) {
      if (site.unit.cls !== "delay" || !live.has(site.path)) continue;
      const inp = gather(site.path);
      this.prim(site).write!(inp, ctxFor(site.path));
    }

    // --- pass D: commit. The logs grow by one tick's worth.
    for (const [addr, v] of pendingAppends) {
      this.store.persist.appendMem(addr, this.store.putBlob(v));
    }

    const tick = this.tickIndex++;
    const sigmaHash = hash({ "@": "sigma/1", tick, at: sigma });
    return { tick, sigma, sigmaHash, effects, ran: ran.sort() };
  }

  /**
   * The core's whole relationship to what a leaf does: hand the opaque body to the host and
   * take back something callable. It never inspects the body, so no vocabulary can leak in
   * here and no vocabulary can limit what may be authored.
   */
  private prim(site: { unit: { body: unknown; cls?: unknown } }): LeafDef {
    const u = site.unit as unknown as import("./model.js").Unit;
    if (!u.body) throw new Error("a leaf with no body cannot run");
    return this.host.resolve(u.body, u);
  }

  /**
   * live <- observed OR pinned OR a live consumer is downstream.
   * Propagates UPSTREAM, so a source feeding a watched pane stays live: the viewport is a
   * garbage collector for computation.
   */
  private liveSet(observed: string[]): Set<string> {
    const f = this.f;
    const live = new Set<string>();
    const isObserved = (path: string) =>
      observed.some((o) => o === path || path.startsWith(o + "/"));

    const pinnedByAncestor = (path: string) =>
      f.pinnedPrefixes.some((p) => path === p || path.startsWith(p + "/"));

    for (const site of f.sites.values())
      if (site.pane.pinned || pinnedByAncestor(site.path) || isObserved(site.path))
        live.add(site.path);

    let grew = true;
    while (grew) {
      grew = false;
      for (const [consumerEp, r] of f.resolved) {
        const consumer = epPath(consumerEp);
        const producer = epPath(r.producer);
        if (live.has(consumer) && !live.has(producer)) { live.add(producer); grew = true; }
      }
    }
    return live;
  }

  // ---------------------------------------------------------------- motion

  /** Preserve Boundary + Identity, replace substrate. The memo is deliberately not shipped;
   *  it is cache tier and rebuilds deterministically. */
  migrate(dest: Store): Engine {
    this.store.copyInto(dest);
    const e = new Engine(dest, this.root, this.frames, this.host);
    e.tickIndex = this.tickIndex;
    for (const [k, v] of this.lastEffectInputs) e.lastEffectInputs.set(k, v);
    return e;
  }

  memoSize(): number {
    return this.memo.size;
  }
}

/** Record the journal, replay it, and every σ comes back identical. */
export function replay(
  root: Graph, store: Store, log: TickInput[], frames?: FrameRegistry,
): TickResult[] {
  const fresh = new Store(new MemoryPersistence());
  store.copyInto(fresh);
  // a replay starts from an empty memory: the logs are rebuilt by the run itself
  const clean = new Store(new MemoryPersistence());
  for (const id of fresh.persist.unitIds()) clean.persist.putUnit(id, fresh.persist.getUnit(id)!);
  for (const id of fresh.persist.blobIds()) clean.persist.putBlob(id, fresh.persist.getBlob(id)!);
  const e = new Engine(clean, root, frames);
  return log.map((i) => e.tick(i));
}
