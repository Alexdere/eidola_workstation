import { describe, expect, it } from "vitest";
import {
  Engine, MemoryPersistence, Store, container, dumpWorkspace, graph, leaf,
  loadWorkspace, pane, port, wire,
} from "../src/index.js";

const src = (s: string) => ({ "@": "host/1" as const, lang: "js", src: s });
const k = (st: Store, n: number) =>
  st.putUnit(leaf("pure", src(`return () => ({ out: ${n} })`), [port("out", "out", "num")]));

/** A boundary is an ALIAS, not a copy: nothing is stored at `chip#sum`, because the value
 *  already lives at the interior producer. Follow the alias down to find it. */
function epOf(st: Store, unitId: string, path: string, portName: string): string {
  const u = st.unit(unitId);
  if (!u.graph) return `${path}#${portName}`;
  const w = u.graph.wires.find((x) => x.to[0] === "@out" && x.to[1] === portName);
  if (!w) return `${path}#${portName}`;
  return epOf(st, u.graph.panes[w.from[0]].unit, `${path}/${w.from[0]}`, w.from[1]);
}

describe("drafts — a unit that exists and has no identity", () => {
  it("a draft runs, but is not citable", () => {
    const st = new Store(new MemoryPersistence());
    const add = st.putUnit(leaf("pure", src("return (i)=>({out:(i.a||0)+(i.b||0)})"),
      [port("a", "in", "num"), port("b", "in", "num"), port("out", "out", "num")]));
    const inner = graph({ a: pane(k(st, 20)), b: pane(k(st, 22)), s: pane(add) },
      [wire(["a", "out"], ["s", "a"]), wire(["b", "out"], ["s", "b"]), wire(["s", "out"], ["@out", "sum"])]);
    const d = st.putDraft(container([port("sum", "out", "num")], inner));
    expect(Store.isDraft(d)).toBe(true);

    const r = new Engine(st, graph({ chip: pane(d) }, [])).tick({ observed: ["chip"] });
    expect(st.blob(r.sigma[epOf(st, d, "chip", "sum")])).toBe(42);      // it computes
  });

  it("declaring seals everything below it into one thing", () => {
    const st = new Store(new MemoryPersistence());
    const innerDraft = st.putDraft(container([port("out", "out", "num")],
      graph({ n: pane(k(st, 7)) }, [wire(["n", "out"], ["@out", "out"])])));
    const outer = st.putDraft(container([port("out", "out", "num")],
      graph({ deep: pane(innerDraft) }, [wire(["deep", "out"], ["@out", "out"])])));
    expect(st.hasDraft(st.unit(outer))).toBe(true);         // a draft inside a draft

    const sealed = st.declare(outer);
    expect(Store.isDraft(sealed)).toBe(false);
    expect(st.hasDraft(st.unit(sealed))).toBe(false);       // the inner one went too
    const r = new Engine(st, graph({ chip: pane(sealed) }, [])).tick({ observed: ["chip"] });
    expect(st.blob(r.sigma[epOf(st, sealed, "chip", "out")])).toBe(7);
  });

  it("two chips built the same way ARE the same chip — identity arrives at declaration", () => {
    const st = new Store(new MemoryPersistence());
    const mk = () => st.putDraft(container([port("out", "out", "num")],
      graph({ n: pane(k(st, 5)) }, [wire(["n", "out"], ["@out", "out"])])));
    const a = mk(), b = mk();
    expect(a).not.toBe(b);                                   // two drafts, two tickets
    expect(st.declare(a)).toBe(st.declare(b));               // one hash
  });

  it("a chip is a silo: nothing outside can reach past its boundary", () => {
    const st = new Store(new MemoryPersistence());
    const chip = st.declare(st.putDraft(container([port("out", "out", "num")],
      graph({ hidden: pane(k(st, 99)) }, [wire(["hidden", "out"], ["@out", "out"])]))));
    // there is no way to name `hidden` from outside: a wire endpoint is [occ, port] at THIS
    // level, so the interior is unaddressable except through the ports it chose to show.
    const outside = graph({ chip: pane(chip) }, []);
    expect(Object.keys(outside.panes)).toEqual(["chip"]);
    expect(st.unit(chip).ports.map((p) => p.name)).toEqual(["out"]);
    expect(() => new Engine(st, graph({ chip: pane(chip), thief: pane(k(st, 1)) },
      [wire(["chip", "hidden"], ["thief", "nope"])])).validate()).toThrow();
  });

  it("a draft survives the file as a draft", () => {
    const st = new Store(new MemoryPersistence());
    const d = st.putDraft(container([port("out", "out", "num")],
      graph({ n: pane(k(st, 3)) }, [wire(["n", "out"], ["@out", "out"])])));
    const dumped = dumpWorkspace(st, graph({ c: pane(d) }, []));
    expect(JSON.stringify(dumped)).toContain("draft");
    const st2 = new Store(new MemoryPersistence());
    const { root } = loadWorkspace(dumped, st2);
    expect(Store.isDraft(root.panes.c.unit)).toBe(true);      // still unfinished work
    const r = new Engine(st2, root).tick({ observed: ["c"] });
    expect(st2.blob(r.sigma[epOf(st2, root.panes.c.unit, "c", "out")])).toBe(3);
  });
});
