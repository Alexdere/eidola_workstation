// The primitive table — spec/CORE-FORMAT.md §6.
//
// A primitive's identity is its op name, not any source text. That is what makes a unit
// portable across languages: the same computation written in JS and in Python is two
// different contents, hence two different units (Law 3 working correctly). Primitives are
// the shared ground; host bodies are host extensions.

import { Value } from "./canon.js";
import { Cls, Port, Unit, leaf, port } from "./model.js";
import { Ctx } from "./caps.js";
import { sameValue } from "./canon.js";

export interface PrimDef {
  op: string;
  cls: Cls;
  ports: Port[];
  powers: string[];
  /** pure and source: inputs in, outputs out. A missing output key means "absent". */
  cook?: (inp: Record<string, Value>, ctx: Ctx) => Record<string, Value>;
  /**
   * delay, pass A. Takes NO inputs — not "should not use them", cannot see them.
   * Impossible beats detectable, and this signature is where that is decided.
   */
  read?: (ctx: Ctx) => Record<string, Value>;
  /** delay, pass C. Inputs have settled; the append is buffered until pass D. */
  write?: (inp: Record<string, Value>, ctx: Ctx) => void;
  /** effect, pass B. No outputs, so nothing can observe it: it must be pinned to live. */
  act?: (inp: Record<string, Value>, ctx: Ctx) => void;
}

const num = (v: Value | undefined): number => (typeof v === "number" ? v : NaN);
const bool = (v: Value | undefined): boolean => v === true;

const P: PrimDef[] = [
  {
    op: "core.const",
    cls: "pure",
    ports: [port("out", "out", "any")],
    powers: [],
    cook: (_i, ctx) => ({ out: ctx.config === null ? null : ctx.config }),
  },
  {
    op: "core.add",
    cls: "pure",
    ports: [port("a", "in", "num"), port("b", "in", "num"), port("out", "out", "num")],
    powers: [],
    cook: (i) => ({ out: num(i.a) + num(i.b) }),
  },
  {
    op: "core.sub",
    cls: "pure",
    ports: [port("a", "in", "num"), port("b", "in", "num"), port("out", "out", "num")],
    powers: [],
    cook: (i) => ({ out: num(i.a) - num(i.b) }),
  },
  {
    op: "core.mul",
    cls: "pure",
    ports: [port("a", "in", "num"), port("b", "in", "num"), port("out", "out", "num")],
    powers: [],
    cook: (i) => ({ out: num(i.a) * num(i.b) }),
  },
  {
    op: "core.neg",
    cls: "pure",
    ports: [port("a", "in", "num"), port("out", "out", "num")],
    powers: [],
    cook: (i) => ({ out: -num(i.a) }),
  },
  {
    op: "core.gt",
    cls: "pure",
    ports: [port("a", "in", "num"), port("b", "in", "num"), port("out", "out", "bool")],
    powers: [],
    cook: (i) => ({ out: num(i.a) > num(i.b) }),
  },
  {
    op: "core.eq",
    cls: "pure",
    ports: [port("a", "in", "any"), port("b", "in", "any"), port("out", "out", "bool")],
    powers: [],
    // canonical-encoding equality, so 1 === 1.0 and NaN === NaN, both because
    // canonicalisation says so rather than because a language does
    cook: (i) => ({ out: sameValue(i.a ?? null, i.b ?? null) }),
  },
  {
    op: "core.not",
    cls: "pure",
    ports: [port("a", "in", "bool"), port("out", "out", "bool")],
    powers: [],
    cook: (i) => ({ out: !bool(i.a) }),
  },
  {
    op: "core.and",
    cls: "pure",
    ports: [port("a", "in", "bool"), port("b", "in", "bool"), port("out", "out", "bool")],
    powers: [],
    cook: (i) => ({ out: bool(i.a) && bool(i.b) }),
  },
  {
    op: "core.or",
    cls: "pure",
    ports: [port("a", "in", "bool"), port("b", "in", "bool"), port("out", "out", "bool")],
    powers: [],
    cook: (i) => ({ out: bool(i.a) || bool(i.b) }),
  },
  {
    op: "core.if",
    cls: "pure",
    ports: [
      port("c", "in", "bool"), port("a", "in", "any"), port("b", "in", "any"),
      port("out", "out", "any"),
    ],
    powers: [],
    cook: (i) => ({ out: bool(i.c) ? (i.a ?? null) : (i.b ?? null) }),
  },
  {
    op: "core.concat",
    cls: "pure",
    ports: [port("a", "in", "str"), port("b", "in", "str"), port("out", "out", "str")],
    powers: [],
    // non-strings become the empty string in BOTH cores: String(5) is "5" in JS and
    // str(5.0) is "5.0" in Python, and a shape of `any` can reach a `str` port
    cook: (i) => ({
      out: (typeof i.a === "string" ? i.a : "") + (typeof i.b === "string" ? i.b : ""),
    }),
  },
  {
    op: "core.list2",
    cls: "pure",
    ports: [port("a", "in", "any"), port("b", "in", "any"), port("out", "out", "list")],
    powers: [],
    cook: (i) => ({ out: [i.a ?? null, i.b ?? null] }),
  },
  {
    op: "core.source",
    cls: "source",
    ports: [port("out", "out", "any")],
    powers: ["emit"],
    cook: (_i, ctx) => {
      const v = ctx.journal();
      // absence is a member of the set, not an error
      const out: Record<string, Value> = {};
      if (v !== undefined) out.out = v;
      return out;
    },
  },
  {
    op: "core.delay",
    cls: "delay",
    ports: [port("x", "in", "any"), port("out", "out", "any")],
    powers: ["mem:r", "mem:w"],
    read: (ctx) => ({ out: ctx.memFold("last", ctx.config ?? null) }),
    write: (i, ctx) => { if (i.x !== undefined) ctx.memAppend(i.x); },
  },
  {
    op: "core.sum",
    cls: "delay",
    ports: [port("x", "in", "num"), port("out", "out", "num")],
    powers: ["mem:r", "mem:w"],
    read: (ctx) => ({ out: ctx.memFold("sum", ctx.config ?? 0) }),
    write: (i, ctx) => { if (i.x !== undefined) ctx.memAppend(i.x); },
  },
  {
    op: "core.lt",
    cls: "pure",
    ports: [port("a", "in", "num"), port("b", "in", "num"), port("out", "out", "bool")],
    powers: [],
    cook: (i) => ({ out: num(i.a) < num(i.b) }),
  },
  {
    op: "core.abs",
    cls: "pure",
    ports: [port("a", "in", "num"), port("out", "out", "num")],
    powers: [],
    cook: (i) => ({ out: Math.abs(num(i.a)) }),
  },
  {
    op: "core.min",
    cls: "pure",
    ports: [port("a", "in", "num"), port("b", "in", "num"), port("out", "out", "num")],
    powers: [],
    // spelled out rather than delegated: Math.min(NaN,x) is NaN but Python's min() returns
    // whichever argument came first, so the language default is not safe to inherit
    cook: (i) => {
      const a = num(i.a), b = num(i.b);
      return { out: Number.isNaN(a) || Number.isNaN(b) ? NaN : (a < b ? a : b) };
    },
  },
  {
    op: "core.max",
    cls: "pure",
    ports: [port("a", "in", "num"), port("b", "in", "num"), port("out", "out", "num")],
    powers: [],
    cook: (i) => {
      const a = num(i.a), b = num(i.b);
      return { out: Number.isNaN(a) || Number.isNaN(b) ? NaN : (a > b ? a : b) };
    },
  },
  {
    op: "core.floor",
    cls: "pure",
    ports: [port("a", "in", "num"), port("out", "out", "num")],
    powers: [],
    // math.floor RAISES on NaN/Infinity in Python and returns an int; both are guarded
    cook: (i) => {
      const a = num(i.a);
      return { out: Number.isFinite(a) ? Math.floor(a) : a };
    },
  },
  {
    op: "core.len",
    cls: "pure",
    ports: [port("a", "in", "list"), port("out", "out", "num")],
    powers: [],
    // lists only: JS string length counts UTF-16 units and Python counts code points, so
    // "length of a string" is not one concept and does not belong in the shared ground
    cook: (i) => ({ out: Array.isArray(i.a) ? i.a.length : NaN }),
  },
  {
    op: "core.at",
    cls: "pure",
    ports: [port("a", "in", "list"), port("i", "in", "num"), port("out", "out", "any")],
    powers: [],
    cook: (i) => {
      if (!Array.isArray(i.a)) return { out: null };
      const n = num(i.i);
      if (!Number.isFinite(n)) return { out: null };
      const k = Math.floor(n);
      return { out: k >= 0 && k < i.a.length ? i.a[k] : null };
    },
  },
  {
    op: "core.append",
    cls: "pure",
    ports: [port("a", "in", "list"), port("x", "in", "any"), port("out", "out", "list")],
    powers: [],
    cook: (i) => ({ out: [...(Array.isArray(i.a) ? i.a : []), i.x ?? null] }),
  },
  {
    op: "core.hist",
    cls: "delay",
    ports: [port("x", "in", "any"), port("out", "out", "list")],
    powers: ["mem:r", "mem:w"],
    // the whole log, as a value. A delay whose fold is identity — which is what makes a
    // chart of history an ordinary pattern rather than a feature.
    read: (ctx) => ({ out: ctx.memFold("all", []) }),
    write: (i, ctx) => { if (i.x !== undefined) ctx.memAppend(i.x); },
  },
  {
    op: "core.print",
    cls: "effect",
    ports: [port("x", "in", "any")],
    powers: ["world"],
    act: (i, ctx) => ctx.world(i.x ?? null),
  },
];

export const PRIMS: Map<string, PrimDef> = new Map(P.map((d) => [d.op, d]));

/** The unit a primitive denotes. Same op name, same unit, same hash, in any language. */
export function primUnit(op: string): Unit {
  const d = PRIMS.get(op);
  if (!d) throw new Error(`unknown primitive: ${op}`);
  return leaf(d.cls as Exclude<Cls, "container">, { "@": "prim/1", op }, d.ports, d.powers);
}
