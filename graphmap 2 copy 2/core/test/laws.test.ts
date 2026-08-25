// The falsification suite. Not coverage — claims that could be false.
// Each test names the law it would break.

import { describe, expect, it } from "vitest";
import {
  Engine, MemoryPersistence, PowerDenied, Store, canonHex, defaultFrames, FrameRegistry,
  graph, hash, makeCtx, pane, primUnit, replay, serializeGraph, unitId, wire, container, port,
} from "../src/index.js";
import { framed, ws } from "./helpers.js";

// ------------------------------------------------------------------ Law 2 · Closure

describe("Law 2 · Closure — a graph plus a boundary is a unit, with no new construct", () => {
  it("nests three levels and a value crosses every seam", () => {
    const { store, u, put } = ws();
    const add = u("core.add");
    const konst = u("core.const");

    const inner = put(container(
      [port("a", "in", "num"), port("b", "in", "num"), port("s", "out", "num")],
      graph({ add: pane(add) }, [
        wire(["@in", "a"], ["add", "a"]),
        wire(["@in", "b"], ["add", "b"]),
        wire(["add", "out"], ["@out", "s"]),
      ]),
    ));
    const mid = put(container(
      [port("x", "in", "num"), port("y", "in", "num"), port("z", "out", "num")],
      graph({ inner: pane(inner) }, [
        wire(["@in", "x"], ["inner", "a"]),
        wire(["@in", "y"], ["inner", "b"]),
        wire(["inner", "s"], ["@out", "z"]),
      ]),
    ));
    const outer = put(container(
      [port("p", "in", "num"), port("q", "in", "num"), port("r", "out", "num")],
      graph({ mid: pane(mid) }, [
        wire(["@in", "p"], ["mid", "x"]),
        wire(["@in", "q"], ["mid", "y"]),
        wire(["mid", "z"], ["@out", "r"]),
      ]),
    ));

    const root = graph(
      { c2: pane(konst, { config: 2 }), c3: pane(konst, { config: 3 }), o: pane(outer) },
      [wire(["c2", "out"], ["o", "p"]), wire(["c3", "out"], ["o", "q"])],
    );

    const e = new Engine(store, root);
    const r = e.tick({ observed: ["o"] });
    expect(store.blob(r.sigma["o/mid/inner/add#out"])).toBe(5);
  });

  it("a container may not declare a power", () => {
    const { store, u, put } = ws();
    const src = u("core.source");
    const bad = {
      ...container([port("out", "out", "any")], graph({ s: pane(src) }, [
        wire(["s", "out"], ["@out", "out"]),
      ])),
      powers: ["emit"],
    };
    const id = put(bad);
    expect(() => new Engine(store, graph({ b: pane(id) }, []))).toThrow(/declares powers/);
  });
});

// ------------------------------------------------------------------ Law 8 · Gauge

describe("Law 8 · Gauge — presentation is not content", () => {
  it("wire order is gauge: reordering leaves the hash byte-identical", () => {
    const { u } = ws();
    const add = u("core.add");
    const k = u("core.const");
    const panes = { a: pane(k, { config: 1 }), b: pane(k, { config: 2 }), s: pane(add) };
    const w1 = wire(["a", "out"], ["s", "a"]);
    const w2 = wire(["b", "out"], ["s", "b"]);
    expect(serializeGraph(graph(panes, [w1, w2])))
      .toBe(serializeGraph(graph(panes, [w2, w1])));
  });

  it("port order is NOT gauge: a boundary is an ordered interface", () => {
    const a = primUnit("core.add");
    const swapped = { ...a, ports: [a.ports[1], a.ports[0], a.ports[2]] };
    expect(unitId(a)).not.toBe(unitId(swapped));
  });

  it("observation is not content: watching something changes no hash", () => {
    const { store, u } = ws();
    const k = u("core.const");
    const e = new Engine(store, graph({ c: pane(k, { config: 7 }) }, []));
    const before = e.graphHash();
    e.tick({ observed: ["c"] });
    e.tick({ observed: [] });
    expect(e.graphHash()).toBe(before);
  });
});

// ------------------------------------------------------------------ Law 3 · Identity

describe("Law 3 · Identity — same content, same thing", () => {
  it("two panes over one node compute once", () => {
    const { store, u } = ws();
    const k = u("core.const");
    const neg = u("core.neg");
    const root = graph(
      {
        c: pane(k, { config: 5 }),
        n1: pane(neg, { pinned: true }),
        n2: pane(neg, { pinned: true }),
      },
      [wire(["c", "out"], ["n1", "a"]), wire(["c", "out"], ["n2", "a"])],
    );
    const e = new Engine(store, root);
    const r = e.tick();
    // both panes carry the value...
    expect(store.blob(r.sigma["n1#out"])).toBe(-5);
    expect(store.blob(r.sigma["n2#out"])).toBe(-5);
    // ...and exactly one of them ran. The memo key is content; the address is not.
    const negRuns = r.ran.filter((p) => p === "n1" || p === "n2");
    expect(negRuns).toHaveLength(1);
  });

  it("a unit's identity covers its code, so the same recipe in two languages is two units", () => {
    const js = { "@": "host/1" as const, lang: "js", src: "x => x + 1" };
    const py = { "@": "host/1" as const, lang: "py", src: "lambda x: x + 1" };
    const mk = (b: typeof js) => ({
      "@": "unit/1" as const, ports: [port("x", "in", "num"), port("out", "out", "num")],
      powers: [], cls: "pure" as const, body: b, graph: null,
    });
    expect(unitId(mk(js))).not.toBe(unitId(mk(py)));
  });
});

// ------------------------------------------------------------------ Law 4 · Authority

describe("Law 4 · Authority — power must be declared to be held", () => {
  it("an undeclared power raises at the point of the reach", () => {
    const ctx = makeCtx({
      powers: new Set<string>(), config: null,
      readLog: () => [], appendLog: () => {}, journalValue: undefined, emitWorld: () => {},
    });
    expect(() => ctx.memFold("last", 0)).toThrow(PowerDenied);
    expect(() => ctx.memAppend(1)).toThrow(PowerDenied);
    expect(() => ctx.journal()).toThrow(PowerDenied);
    expect(() => ctx.world(1)).toThrow(PowerDenied);
  });

  it("there is no clock for anybody: time is a source or it does not exist", () => {
    const ctx = makeCtx({
      powers: new Set(["mem:r", "mem:w", "emit", "world"]), config: null,
      readLog: () => [], appendLog: () => {}, journalValue: undefined, emitWorld: () => {},
    });
    expect("now" in ctx).toBe(false);
    expect(Object.keys(ctx).sort()).toEqual(
      ["config", "journal", "memAppend", "memFold", "world"],
    );
  });

  it("naming a mem address the boundary does not declare is refused", () => {
    const { store, u } = ws();
    const neg = u("core.neg");
    const k = u("core.const");
    expect(() => new Engine(store, graph(
      { c: pane(k, { config: 1 }), n: pane(neg, { mem: "somewhere" }) },
      [wire(["c", "out"], ["n", "a"])],
    ))).toThrow(/mem/);
  });
});

// ------------------------------------------------------------------ Law 5 · Novelty

describe("Law 5 · Novelty — change enters only at declared points", () => {
  it("record the journal, replay it, and every sigma comes back identical", () => {
    const { store, u } = ws();
    const src = u("core.source");
    const sum = u("core.sum");
    const root = graph(
      { s: pane(src), acc: pane(sum, { mem: "acc", config: 0, pinned: true }) },
      [wire(["s", "out"], ["acc", "x"])],
    );
    const log = [
      { journal: { "s#out": 1 } },
      { journal: { "s#out": 2 } },
      { journal: { "s#out": 3 } },
    ];
    const e = new Engine(store, root);
    const live = log.map((i) => e.tick(i));
    const again = replay(root, store, log);
    expect(again.map((r) => r.sigmaHash)).toEqual(live.map((r) => r.sigmaHash));
    // and the running sum actually ran: 0, 1, 3
    expect(live.map((r) => store.blob(r.sigma["acc#out"]))).toEqual([0, 1, 3]);
  });
});

// ------------------------------------------------------------------ Law 6 · Demand

describe("Law 6 · Demand — nothing runs because it exists", () => {
  it("an unobserved, unpinned subtree does not run", () => {
    const { store, u } = ws();
    const k = u("core.const");
    const neg = u("core.neg");
    const e = new Engine(store, graph(
      { c: pane(k, { config: 3 }), n: pane(neg) },
      [wire(["c", "out"], ["n", "a"])],
    ));
    expect(e.tick({ observed: [] }).ran).toEqual([]);
    expect(e.tick({ observed: ["n"] }).ran.sort()).toEqual(["c", "n"]);
  });

  it("liveness propagates upstream: a source feeding a watched pane stays live", () => {
    const { store, u } = ws();
    const src = u("core.source");
    const neg = u("core.neg");
    const e = new Engine(store, graph(
      { s: pane(src), n: pane(neg) },
      [wire(["s", "out"], ["n", "a"])],
    ));
    const r = e.tick({ observed: ["n"], journal: { "s#out": 4 } });
    expect(r.ran).toContain("s");
    expect(store.blob(r.sigma["n#out"])).toBe(-4);
  });

  it("an effect has no output, so it is dead forever unless pinned", () => {
    const { store, u } = ws();
    const k = u("core.const");
    const pr = u("core.print");

    const dead = new Engine(store, graph(
      { c: pane(k, { config: 1 }), p: pane(pr) }, [wire(["c", "out"], ["p", "x"])],
    ));
    expect(dead.tick({ observed: [] }).effects).toEqual([]);

    const alive = new Engine(store, graph(
      { c: pane(k, { config: 1 }), p: pane(pr, { pinned: true }) },
      [wire(["c", "out"], ["p", "x"])],
    ));
    expect(alive.tick().effects).toEqual([1]);
    // and a world-facing act is not repeated just because a tick happened
    expect(alive.tick().effects).toEqual([]);
  });
});

// ------------------------------------------------------------------ F8 · the tick

describe("F8 · the tick is five passes, and that is its whole content", () => {
  it("a cycle without a delay is refused at wire time, not discovered mid-tick", () => {
    const { store, u } = ws();
    const add = u("core.add");
    const k = u("core.const");
    expect(() => new Engine(store, graph(
      { c: pane(k, { config: 1 }), s: pane(add) },
      [wire(["c", "out"], ["s", "b"]), wire(["s", "out"], ["s", "a"])],
    ))).toThrow(/cycle without a delay/);
  });

  it("a delay legalises the cycle, and the counter counts", () => {
    const { store, u } = ws();
    const add = u("core.add");
    const k = u("core.const");
    const d = u("core.delay");
    const e = new Engine(store, graph(
      {
        one: pane(k, { config: 1 }),
        s: pane(add, { pinned: true }),
        d: pane(d, { mem: "c", config: 0 }),
      },
      [
        wire(["one", "out"], ["s", "b"]),
        wire(["d", "out"], ["s", "a"]),
        wire(["s", "out"], ["d", "x"]),
      ],
    ));
    const vals = [0, 1, 2].map(() => store.blob(e.tick().sigma["s#out"]));
    expect(vals).toEqual([1, 2, 3]);
  });

  it("a delay's read phase cannot see this tick's input", () => {
    const { store, u } = ws();
    const src = u("core.source");
    const d = u("core.delay");
    const e = new Engine(store, graph(
      { s: pane(src), d: pane(d, { mem: "m", config: 99, pinned: true }) },
      [wire(["s", "out"], ["d", "x"])],
    ));
    // tick 1 emits 7, but the read happened before the write: the delay still shows its init
    expect(store.blob(e.tick({ journal: { "s#out": 7 } }).sigma["d#out"])).toBe(99);
    expect(store.blob(e.tick({ journal: { "s#out": 8 } }).sigma["d#out"])).toBe(7);
  });
});

// ------------------------------------------------------------------ Law 7 · Frame

describe("Law 7 · Frame — routes must agree", () => {
  it("the shipped registry is coherent", () => {
    expect(defaultFrames().checkRouteAgreement()).toEqual([]);
  });

  it("a wrong constant shows up as disagreement, not as a plausible number", () => {
    const r = new FrameRegistry();
    const fx = [0, 100];
    r.addFrame({ id: "temp.c", tol: 1e-9, fixtures: fx })
      .addFrame({ id: "temp.f", tol: 1e-9, fixtures: fx })
      .addFrame({ id: "temp.k", tol: 1e-9, fixtures: fx });
    const n = (v: unknown) => (typeof v === "number" ? v : NaN);
    r.addTransition({ from: "temp.c", to: "temp.f", fn: (v) => (n(v) * 9) / 5 + 32 });
    r.addTransition({ from: "temp.f", to: "temp.k", fn: (v) => ((n(v) - 32) * 5) / 9 + 273.15 });
    r.addTransition({ from: "temp.c", to: "temp.k", fn: (v) => n(v) + 273.0 }); // wrong
    expect(r.checkRouteAgreement().length).toBeGreaterThan(0);
  });

  it("a declared transition is applied as the value crosses the wire", () => {
    const { store, put } = ws();
    const kC = put(framed("core.const", { out: "temp.c" }));
    const negF = put(framed("core.neg", { a: "temp.f" }));
    const e = new Engine(store, graph(
      { c: pane(kC, { config: 100 }), n: pane(negF, { pinned: true }) },
      [wire(["c", "out"], ["n", "a"])],
    ));
    const r = e.tick();
    expect(store.blob(r.sigma["n#a"])).toBe(212);   // received, post-conversion
    expect(store.blob(r.sigma["n#out"])).toBe(-212);
  });

  it("two ports with no route between their frames cannot be wired", () => {
    const { store, put } = ws();
    const kC = put(framed("core.const", { out: "temp.c" }));
    const negX = put(framed("core.neg", { a: "length.m" }));
    expect(() => new Engine(store, graph(
      { c: pane(kC, { config: 1 }), n: pane(negX) },
      [wire(["c", "out"], ["n", "a"])],
    ))).toThrow(/no frame route/);
  });
});

// ------------------------------------------------------------------ motion

describe("Motion — preserve boundary and identity, replace substrate", () => {
  it("a migrated engine ticks to a bit-identical sigma, and the memo is not shipped", () => {
    const { store, u } = ws();
    const src = u("core.source");
    const sum = u("core.sum");
    const neg = u("core.neg");
    const root = graph(
      {
        s: pane(src),
        acc: pane(sum, { mem: "acc", config: 0 }),
        n: pane(neg, { pinned: true }),
      },
      [wire(["s", "out"], ["acc", "x"]), wire(["acc", "out"], ["n", "a"])],
    );
    const e1 = new Engine(store, root);
    for (const v of [1, 2, 3]) e1.tick({ journal: { "s#out": v } });

    const e2 = e1.migrate(new Store(new MemoryPersistence()));
    expect(e2.memoSize()).toBe(0);          // cache tier, deterministically rebuildable
    expect(e1.memoSize()).toBeGreaterThan(0);

    const a = e1.tick({ journal: { "s#out": 10 } });
    const b = e2.tick({ journal: { "s#out": 10 } });
    expect(b.sigmaHash).toBe(a.sigmaHash);
  });
});

// ------------------------------------------------------------------ state scope

describe("State scope is a property of the unit, not the occurrence", () => {
  it("two bindings with different addresses hold independent state", () => {
    const { store, u } = ws();
    const src = u("core.source");
    const sum = u("core.sum");
    const e = new Engine(store, graph(
      {
        s: pane(src),
        a: pane(sum, { mem: "a", config: 0, pinned: true }),
        b: pane(sum, { mem: "b", config: 100, pinned: true }),
      },
      [wire(["s", "out"], ["a", "x"]), wire(["s", "out"], ["b", "x"])],
    ));
    e.tick({ journal: { "s#out": 5 } });
    const r = e.tick({ journal: { "s#out": 5 } });
    expect(store.blob(r.sigma["a#out"])).toBe(5);
    expect(store.blob(r.sigma["b#out"])).toBe(105);
  });

  it("two bindings with the same address share one log — identity is content, state included", () => {
    const { store, u } = ws();
    const src = u("core.source");
    const sum = u("core.sum");
    const e = new Engine(store, graph(
      {
        s: pane(src),
        a: pane(sum, { mem: "shared", config: 0, pinned: true }),
        b: pane(sum, { mem: "shared", config: 0, pinned: true }),
      },
      [wire(["s", "out"], ["a", "x"]), wire(["s", "out"], ["b", "x"])],
    ));
    e.tick({ journal: { "s#out": 5 } });          // both append 5 -> log is [5, 5]
    const r = e.tick({ journal: { "s#out": 5 } });
    expect(store.blob(r.sigma["a#out"])).toBe(10);
    expect(store.blob(r.sigma["b#out"])).toBe(10);
  });
});

// ------------------------------------------------------------------ the head surface

describe("apply(command) in, events out — no DOM anywhere", () => {
  it("a rejected command leaves the graph untouched", () => {
    const { store, u } = ws();
    const k = u("core.const");
    const add = u("core.add");
    const e = new Engine(store, graph({ c: pane(k, { config: 1 }) }, []));
    const before = e.graphHash();
    const ev = e.apply({ t: "add-wire", wire: wire(["c", "out"], ["nope", "a"]) });
    expect(ev.t).toBe("rejected");
    expect(e.graphHash()).toBe(before);

    expect(e.apply({ t: "add-pane", occ: "s", pane: pane(add) }).t).toBe("graph-changed");
    expect(e.graphHash()).not.toBe(before);
  });
});

// ------------------------------------------------------------------ CVE/1

describe("CVE/1 — the ground both cores stand on", () => {
  it("canonicalises the cases languages disagree about", () => {
    expect(canonHex(-0)).toBe(canonHex(0));
    expect(canonHex(NaN)).toBe("64" + "7ff8000000000000"); // 0x64 = 'd', then the bits
    expect(canonHex(1)).toBe(canonHex(1.0));
  });

  it("sorts map keys by UTF-8 bytes, not UTF-16 code units", () => {
    // U+FF3A (utf8 ef bc ba) vs U+1D400 (utf8 f0 9d 90 80): UTF-16 order puts the
    // surrogate pair first, UTF-8 order does not. This is the classic JCS trap.
    const a = { "Ｚ": 1, "\u{1D400}": 2 };
    const b = { "\u{1D400}": 2, "Ｚ": 1 };
    expect(canonHex(a)).toBe(canonHex(b));
    expect(canonHex(a).indexOf("efbcba")).toBeLessThan(canonHex(a).indexOf("f09d9080"));
  });

  it("every binary64 is a value, including ones past the exact-integer boundary", () => {
    // the fuzzer's find: 1e10 * 1e10 is a computed, exactly-representable double, and an
    // over-eager range check refused it. The exactness rule belongs on the encoder's input
    // in a language that HAS an exact integer type — JavaScript does not.
    expect(canonHex(1e10 * 1e10)).toBe("64" + "4415af1d78b58c40");
    expect(() => canonHex(1e300)).not.toThrow();
  });

  it("hashes are domain-tagged", () => {
    expect(hash(null)).toHaveLength(64);
    expect(hash(null)).not.toBe(hash(0));
  });
});
