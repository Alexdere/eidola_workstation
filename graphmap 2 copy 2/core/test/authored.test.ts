import { describe, it, expect } from "vitest";
import { Engine, Store, MemoryPersistence, graph, pane, wire, leaf, port,
         dumpWorkspace, loadWorkspace } from "../src/index.js";

const src = (s: string) => ({ "@": "host/1" as const, lang: "js", src: s });

describe("authored leaves — no vocabulary in the kernel", () => {
  it("runs a leaf whose body is source, with any number of ports", () => {
    const st = new Store(new MemoryPersistence());
    // FIVE inputs. Nothing in the core had to learn a thing.
    const five = st.putUnit(leaf("pure",
      src("return (i) => ({ total: (i.a||0)+(i.b||0)+(i.c||0)+(i.d||0)+(i.e||0) })"),
      [port("a","in","num"),port("b","in","num"),port("c","in","num"),
       port("d","in","num"),port("e","in","num"),port("total","out","num")]));
    const k = (n: number) => st.putUnit(leaf("pure", src(`return () => ({ out: ${n} })`),
      [port("out","out","num")]));
    const g = graph(
      { a: pane(k(1)), b: pane(k(2)), c: pane(k(3)), d: pane(k(4)), e: pane(k(5)), sum: pane(five) },
      [wire(["a","out"],["sum","a"]), wire(["b","out"],["sum","b"]), wire(["c","out"],["sum","c"]),
       wire(["d","out"],["sum","d"]), wire(["e","out"],["sum","e"])]);
    const en = new Engine(st, g);
    const r = en.tick({ observed: ["sum"] });
    expect(st.blob(r.sigma["sum#total"])).toBe(15);
  });

  it("a delay body owns its own memory", () => {
    const st = new Store(new MemoryPersistence());
    const acc = st.putUnit(leaf("delay", src(
      "return { read:(c)=>({out:c.memFold('sum',0)}), write:(i,c)=>{ if(i.x!==undefined) c.memAppend(i.x) } }"),
      [port("x","in","num"),port("out","out","num")], ["mem:r","mem:w"]));
    const one = st.putUnit(leaf("pure", src("return () => ({ out: 2 })"), [port("out","out","num")]));
    const g = graph({ k: pane(one), acc: pane(acc, { mem: "m1" }) }, [wire(["k","out"],["acc","x"])]);
    const en = new Engine(st, g);
    en.tick({ observed: ["acc"] }); en.tick({ observed: ["acc"] });
    const r = en.tick({ observed: ["acc"] });
    expect(st.blob(r.sigma["acc#out"])).toBe(4);
  });

  it("declared powers are the only powers — A4 still impossible to violate", () => {
    const st = new Store(new MemoryPersistence());
    const thief = st.putUnit(leaf("pure", src("return (i,c) => ({ out: c.memFold('last', 0) })"),
      [port("out","out","num")]));   // no mem:r declared
    const en = new Engine(st, graph({ t: pane(thief) }, []));
    expect(() => en.tick({ observed: ["t"] })).toThrow(/PowerDenied/);
  });

  it("an authored leaf survives the file — src is content", () => {
    const st = new Store(new MemoryPersistence());
    const u = st.putUnit(leaf("pure", src("return (i) => ({ out: (i.a||0)*3 })"),
      [port("a","in","num"),port("out","out","num")]));
    const k = st.putUnit(leaf("pure", src("return () => ({ out: 7 })"), [port("out","out","num")]));
    const g = graph({ k: pane(k), m: pane(u) }, [wire(["k","out"],["m","a"])]);
    const dumped = dumpWorkspace(st, g);
    const st2 = new Store(new MemoryPersistence());
    const { root } = loadWorkspace(dumped, st2);
    const r = new Engine(st2, root).tick({ observed: ["m"] });
    expect(st2.blob(r.sigma["m#out"])).toBe(21);
    expect(JSON.stringify(dumped)).toContain("host/1" in {} ? "" : "*3");
  });
});
