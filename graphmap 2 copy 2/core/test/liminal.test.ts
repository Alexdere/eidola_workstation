// Liminal space — the claims that could be false.

import { describe, expect, it } from "vitest";
import {
  Engine, Liminal, collapse, graph, melt, meltSelection, pane, port, serializeGraph,
  unitId, wire, container,
} from "../src/index.js";
import { ws } from "./helpers.js";

/** a workspace where two adds feed a third — the canonical thing to group */
function adders() {
  const w = ws();
  const k = w.u("core.const");
  const add = w.u("core.add");
  const root = graph(
    {
      a: pane(k, { config: 2 }),
      b: pane(k, { config: 3 }),
      c: pane(k, { config: 10 }),
      s1: pane(add),
      s2: pane(add, { pinned: true }),
    },
    [
      wire(["a", "out"], ["s1", "a"]),
      wire(["b", "out"], ["s1", "b"]),
      wire(["s1", "out"], ["s2", "a"]),
      wire(["c", "out"], ["s2", "b"]),
    ],
  );
  return { ...w, root, k, add };
}

describe("liminal is playable, not citable", () => {
  it("composing does not touch the addressed world", () => {
    const { store, k, add } = adders();
    const before = store.persist.unitIds().length;

    const lim = new Liminal(
      [port("x", "in", "num"), port("y", "in", "num"), port("out", "out", "num")],
      graph({ s: pane(add) }, [
        wire(["@in", "x"], ["s", "a"]),
        wire(["@in", "y"], ["s", "b"]),
        wire(["s", "out"], ["@out", "out"]),
      ]),
    );

    // play it twenty times, edit it in between — nothing is hashed into the real store
    for (let i = 0; i < 20; i++) {
      lim.addPane(`scratch${i}`, pane(k, { config: i }));
      lim.play(store, { x: 1, y: 2 });
      lim.removePane(`scratch${i}`);
    }
    expect(store.persist.unitIds().length).toBe(before);

    // ...and only declaring puts something there
    const id = lim.declare(store);
    expect(store.persist.unitIds().length).toBe(before + 1);
    expect(store.hasUnit(id)).toBe(true);
  });

  it("evaluates against the tentative boundary, so you can hear it while writing it", () => {
    const { store, add } = adders();
    const lim = new Liminal(
      [port("x", "in", "num"), port("y", "in", "num"), port("out", "out", "num")],
      graph({ s: pane(add, { pinned: true }) }, [
        wire(["@in", "x"], ["s", "a"]),
        wire(["@in", "y"], ["s", "b"]),
        wire(["s", "out"], ["@out", "out"]),
      ]),
    );
    const { result, scratch } = lim.play(store, { x: 20, y: 22 });
    expect(scratch.blob(result.sigma["s#out"])).toBe(42);
  });

  it("an unstubbed boundary input is absent, not an error", () => {
    const { store, add } = adders();
    const lim = new Liminal(
      [port("x", "in", "num"), port("out", "out", "num")],
      graph({ s: pane(add, { pinned: true }) }, [
        wire(["@in", "x"], ["s", "a"]),
        wire(["s", "out"], ["@out", "out"]),
      ]),
    );
    const { result } = lim.play(store, {});
    expect(result.sigma["s#out"]).toBeUndefined();
  });
});

describe("melt and declare", () => {
  it("melting leaves the original untouched, because it cannot be touched", () => {
    const { store, add } = adders();
    const box = store.putUnit(container(
      [port("x", "in", "num"), port("y", "in", "num"), port("o", "out", "num")],
      graph({ s: pane(add) }, [
        wire(["@in", "x"], ["s", "a"]),
        wire(["@in", "y"], ["s", "b"]),
        wire(["s", "out"], ["@out", "o"]),
      ]),
    ));
    const lim = melt(store, box);
    lim.removePane("s");
    expect(store.unit(box).graph!.panes.s).toBeDefined();
    expect(unitId(store.unit(box))).toBe(box);
  });

  it("declaring after a melt records lineage BESIDE the store, never inside identity", () => {
    const { store, add, k } = adders();
    const box = store.putUnit(container(
      [port("x", "in", "num"), port("o", "out", "num")],
      graph({ s: pane(add) }, [
        wire(["@in", "x"], ["s", "a"]),
        wire(["s", "out"], ["@out", "o"]),
      ]),
    ));
    const lim = melt(store, box);
    lim.addPane("extra", pane(k, { config: 1 }));
    lim.addWire(wire(["extra", "out"], ["s", "b"]));
    const v2 = lim.declare(store);

    expect(v2).not.toBe(box);
    expect(store.parentOf(v2)).toBe(box);
    // and the child's hash is a function of its content alone: rebuilding the same content
    // without any lineage gives the same id
    const rebuilt = unitId(store.unit(v2));
    expect(rebuilt).toBe(v2);
  });

  it("two units with identical content are one unit however each came to be", () => {
    const { store, add } = adders();
    const mk = () => container(
      [port("x", "in", "num"), port("o", "out", "num")],
      graph({ s: pane(add) }, [
        wire(["@in", "x"], ["s", "a"]),
        wire(["s", "out"], ["@out", "o"]),
      ]),
    );
    const first = store.putUnit(mk());
    const viaMelt = melt(store, first).declare(store);
    expect(viaMelt).toBe(first);            // identity is content; history is not content
    expect(store.parentOf(first)).toBeUndefined();
  });

  it("a malformed composite is refused at declare time, not at the first tick", () => {
    const { store, add } = adders();
    const lim = new Liminal(
      [port("o", "out", "num")],
      graph({ s: pane(add) }, [wire(["s", "out"], ["@out", "o"]), wire(["s", "out"], ["s", "a"])]),
    );
    expect(() => lim.declare(store)).toThrow(/cycle/);
  });
});

describe("collapse — Law 2 as something you can do with a mouse", () => {
  it("the boundary is read off the wires that already crossed the selection", () => {
    const { store, root } = adders();
    const lim = meltSelection(root, ["a", "b", "s1"], store);
    // a and b are interior constants; only s1's output crossed outward
    expect(lim.ports.map((p) => `${p.dir}:${p.name}`)).toEqual(["out:s1_out"]);
  });

  it("collapsing preserves what the workspace computes", () => {
    const { store, root } = adders();
    const before = new Engine(store, root).tick();
    const beforeValue = store.blob(before.sigma["s2#out"]);
    expect(beforeValue).toBe(15);

    const { graph: after, unit } = collapse(root, ["a", "b", "s1"], store, "box");
    const e = new Engine(store, after);
    const r = e.tick();
    expect(store.blob(r.sigma["s2#out"])).toBe(15);
    // and what used to be three panes is now one pane bound to one unit
    expect(Object.keys(after.panes).sort()).toEqual(["box", "c", "s2"]);
    expect(after.panes.box.unit).toBe(unit);
  });

  it("an incoming crossing becomes a boundary in-port wired from outside", () => {
    const { store, root } = adders();
    const { graph: after } = collapse(root, ["s1", "s2"], store, "box");
    const lim = meltSelection(root, ["s1", "s2"], store);
    expect(lim.ports.filter((p) => p.dir === "in").map((p) => p.name).sort())
      .toEqual(["s1_a", "s1_b", "s2_b"]);
    expect(after.wires.some((w) => w.to[0] === "box" && w.to[1] === "s1_a")).toBe(true);
  });

  it("the collapsed unit is usable anywhere any unit is usable — no new construct", () => {
    const { store, root } = adders();
    const { graph: after, unit } = collapse(root, ["a", "b", "s1"], store, "box");
    // bind the same unit a second time; it is just a unit
    const twice = graph({ ...after.panes, box2: pane(unit, { pinned: true }) }, after.wires);
    const e = new Engine(store, twice);
    const r = e.tick();
    expect(store.blob(r.sigma["box2/s1#out"])).toBe(5);
    // one unit, two paths, one memo entry: exactly one of them cooked
    expect(r.ran.filter((p) => p.endsWith("s1"))).toHaveLength(1);
  });

  it("collapsing is not gauge — it changes the graph hash, and that is correct", () => {
    const { store, root } = adders();
    const before = serializeGraph(root);
    const { graph: after } = collapse(root, ["a", "b", "s1"], store, "box");
    expect(serializeGraph(after)).not.toBe(before);
  });
});
