// Generates spec/vectors/*.json from the TypeScript core.
//
// The vectors are the static half of conformance. They are generated here rather than
// written by hand, and the oracle is written from spec/CANON.md rather than from these
// numbers — so the oracle reproducing them is agreement, not transcription.

import { writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import {
  Engine, MemoryPersistence, Store, canonHex, hash, loadWorkspace, primUnit, serializeGraph,
  toSpec, unitId, Value, WorkspaceSpec,
} from "../src/index.js";
import { runScenarios, Scenario } from "./run.js";

const OUT = join(import.meta.dirname, "..", "..", "spec", "vectors");
mkdirSync(OUT, { recursive: true });

// ------------------------------------------------------------------ canon.json

const canonCases: Array<[string, Value]> = [
  ["null", null],
  ["true", true],
  ["false", false],
  ["zero", 0],
  ["neg-zero", -0],
  ["one", 1],
  ["one-point-five", 1.5],
  ["nan", NaN],
  ["inf", Infinity],
  ["neg-inf", -Infinity],
  ["max-safe-int", 9007199254740991],
  ["min-safe-int", -9007199254740991],
  ["empty-string", ""],
  ["ascii", "hello"],
  ["astral", "\u{1D400}\u{1F600}"],
  ["combining", "é"],
  ["empty-bytes", new Uint8Array([])],
  ["bytes", new Uint8Array([0, 1, 254, 255])],
  ["empty-list", []],
  ["nested-list", [1, [2, [3]]]],
  ["empty-map", {}],
  ["map", { a: 1, b: [true, null] }],
  ["map-key-order-utf8", { "\u{1D400}": 2, "Ｚ": 1 }],
  ["map-key-order-utf8-reversed", { "Ｚ": 1, "\u{1D400}": 2 }],
  ["deep", { a: { b: { c: [1, "x", new Uint8Array([7])] } } }],
];

writeFileSync(
  join(OUT, "canon.json"),
  JSON.stringify(
    {
      "@": "vectors/canon/1",
      note: "value described as VSPEC; enc = canonical bytes as hex; hash = H(value)",
      cases: canonCases.map(([name, v]) => ({
        name, value: toSpec(v), enc: canonHex(v), hash: hash(v),
      })),
    },
    null, 2,
  ) + "\n",
);

// ------------------------------------------------------------------ units.json

const unitCases: Array<[string, string]> = [
  ...["core.const", "core.add", "core.sub", "core.mul", "core.neg", "core.gt", "core.eq",
      "core.not", "core.and", "core.or", "core.if", "core.concat", "core.list2",
      "core.source", "core.delay", "core.sum", "core.print",
      "core.lt", "core.abs", "core.min", "core.max", "core.floor", "core.len", "core.at",
      "core.append", "core.hist"]
    .map((op) => [op, unitId(primUnit(op))] as [string, string]),
];

// the gauge pair: same graph, wires written in two orders
const gaugeWs = (order: 0 | 1): WorkspaceSpec => ({
  "@": "workspace/1",
  units: { k: { prim: "core.const" }, add: { prim: "core.add" } },
  root: {
    panes: {
      a: { unit: "k", config: { d: "3ff0000000000000" } },
      b: { unit: "k", config: { d: "4000000000000000" } },
      s: { unit: "add", pinned: true },
    },
    wires: order === 0
      ? [[["a", "out"], ["s", "a"]], [["b", "out"], ["s", "b"]]]
      : [[["b", "out"], ["s", "b"]], [["a", "out"], ["s", "a"]]],
  },
});

const gauge = [0, 1].map((o) => {
  const store = new Store(new MemoryPersistence());
  return serializeGraph(loadWorkspace(gaugeWs(o as 0 | 1), store).root);
});

writeFileSync(
  join(OUT, "units.json"),
  JSON.stringify(
    {
      "@": "vectors/units/1",
      primitives: Object.fromEntries(unitCases),
      gauge: { note: "the same graph with its wires in two orders", hashes: gauge },
    },
    null, 2,
  ) + "\n",
);

// ------------------------------------------------------------------ scenarios + ticks

const D = (n: number) => {
  const dv = new DataView(new ArrayBuffer(8));
  dv.setFloat64(0, n);
  return { d: [...new Uint8Array(dv.buffer)].map((x) => x.toString(16).padStart(2, "0")).join("") };
};

const scenarios: Scenario[] = [
  {
    name: "const-add",
    workspace: {
      "@": "workspace/1",
      units: { k: { prim: "core.const" }, add: { prim: "core.add" } },
      root: {
        panes: {
          a: { unit: "k", config: D(2) },
          b: { unit: "k", config: D(40) },
          s: { unit: "add", pinned: true },
        },
        wires: [[["a", "out"], ["s", "a"]], [["b", "out"], ["s", "b"]]],
      },
    },
    log: [{}, {}],
  },
  {
    name: "three-level-nesting",
    workspace: {
      "@": "workspace/1",
      units: {
        k: { prim: "core.const" },
        add: { prim: "core.add" },
        inner: {
          container: {
            ports: [["a", "in", "num", null], ["b", "in", "num", null], ["s", "out", "num", null]],
            graph: {
              panes: { add: { unit: "add" } },
              wires: [
                [["@in", "a"], ["add", "a"]],
                [["@in", "b"], ["add", "b"]],
                [["add", "out"], ["@out", "s"]],
              ],
            },
          },
        },
        mid: {
          container: {
            ports: [["x", "in", "num", null], ["y", "in", "num", null], ["z", "out", "num", null]],
            graph: {
              panes: { inner: { unit: "inner" } },
              wires: [
                [["@in", "x"], ["inner", "a"]],
                [["@in", "y"], ["inner", "b"]],
                [["inner", "s"], ["@out", "z"]],
              ],
            },
          },
        },
      },
      root: {
        panes: {
          c2: { unit: "k", config: D(2) },
          c3: { unit: "k", config: D(3) },
          m: { unit: "mid" },
        },
        wires: [[["c2", "out"], ["m", "x"]], [["c3", "out"], ["m", "y"]]],
      },
    },
    log: [{ observed: ["m"] }],
  },
  {
    name: "counter-through-delay",
    workspace: {
      "@": "workspace/1",
      units: { k: { prim: "core.const" }, add: { prim: "core.add" }, d: { prim: "core.delay" } },
      root: {
        panes: {
          one: { unit: "k", config: D(1) },
          s: { unit: "add", pinned: true },
          d: { unit: "d", mem: "c", config: D(0) },
        },
        wires: [
          [["one", "out"], ["s", "b"]],
          [["d", "out"], ["s", "a"]],
          [["s", "out"], ["d", "x"]],
        ],
      },
    },
    log: [{}, {}, {}, {}],
  },
  {
    name: "source-running-sum",
    workspace: {
      "@": "workspace/1",
      units: { src: { prim: "core.source" }, sum: { prim: "core.sum" }, pr: { prim: "core.print" } },
      root: {
        panes: {
          s: { unit: "src" },
          acc: { unit: "sum", mem: "acc", config: D(0), pinned: true },
          p: { unit: "pr", pinned: true },
        },
        wires: [[["s", "out"], ["acc", "x"]], [["acc", "out"], ["p", "x"]]],
      },
    },
    log: [
      { journal: { "s#out": D(1) } },
      { journal: { "s#out": D(2) } },
      { journal: { "s#out": D(3) } },
      { journal: { "s#out": D(0) } },
    ],
  },
  {
    name: "memo-two-panes-one-node",
    workspace: {
      "@": "workspace/1",
      units: { k: { prim: "core.const" }, neg: { prim: "core.neg" } },
      root: {
        panes: {
          c: { unit: "k", config: D(5) },
          n1: { unit: "neg", pinned: true },
          n2: { unit: "neg", pinned: true },
        },
        wires: [[["c", "out"], ["n1", "a"]], [["c", "out"], ["n2", "a"]]],
      },
    },
    log: [{}],
  },
  {
    name: "frame-conversion",
    workspace: {
      "@": "workspace/1",
      units: {
        kc: { prim: "core.const", frames: { out: "temp.c" } },
        nf: { prim: "core.neg", frames: { a: "temp.f" } },
      },
      root: {
        panes: { c: { unit: "kc", config: D(100) }, n: { unit: "nf", pinned: true } },
        wires: [[["c", "out"], ["n", "a"]]],
      },
    },
    log: [{}],
  },
  {
    name: "demand-nothing-observed",
    workspace: {
      "@": "workspace/1",
      units: { k: { prim: "core.const" }, neg: { prim: "core.neg" } },
      root: {
        panes: { c: { unit: "k", config: D(3) }, n: { unit: "neg" } },
        wires: [[["c", "out"], ["n", "a"]]],
      },
    },
    log: [{}, { observed: ["n"] }],
  },
];

writeFileSync(
  join(OUT, "scenarios.json"),
  JSON.stringify({ "@": "vectors/scenarios/1", scenarios }, null, 2) + "\n",
);

writeFileSync(
  join(OUT, "ticks.json"),
  JSON.stringify({ "@": "vectors/ticks/1", results: runScenarios(scenarios) }, null, 2) + "\n",
);

console.log(
  `wrote ${canonCases.length} canon vectors, ${unitCases.length} unit hashes, ` +
  `${scenarios.length} scenarios`,
);
