// Runs scenarios through the TS core and reports exactly what the spec says is observable.
// Used by the vector generator and by the grader (which invokes it as a subprocess).

import {
  Engine, MemoryPersistence, Store, VSpec, WorkspaceSpec, fromSpec, loadWorkspace,
  serializeGraph, toSpec,
} from "../src/index.js";

export interface Scenario {
  name: string;
  workspace: WorkspaceSpec;
  log: Array<{ observed?: string[]; journal?: Record<string, VSpec> }>;
}

export interface ScenarioResult {
  name: string;
  graphHash: string;
  unitIds: Record<string, string>;
  error?: string;
  ticks: Array<{
    tick: number;
    sigmaHash: string;
    sigma: Record<string, string>;
    ran: string[];
    effects: VSpec[];
  }>;
}

export function runScenario(s: Scenario): ScenarioResult {
  const store = new Store(new MemoryPersistence());
  try {
    const { root, unitIds } = loadWorkspace(s.workspace, store);
    const e = new Engine(store, root);
    const ticks = s.log.map((step) => {
      const r = e.tick({
        observed: step.observed,
        journal: step.journal
          ? Object.fromEntries(Object.entries(step.journal).map(([k, v]) => [k, fromSpec(v)]))
          : undefined,
      });
      return {
        tick: r.tick,
        sigmaHash: r.sigmaHash,
        sigma: r.sigma,
        ran: r.ran,
        effects: r.effects.map(toSpec),
      };
    });
    return { name: s.name, graphHash: serializeGraph(root), unitIds, ticks };
  } catch (err) {
    return {
      name: s.name, graphHash: "", unitIds: {}, ticks: [],
      error: (err as Error).message,
    };
  }
}

export function runScenarios(ss: Scenario[]): ScenarioResult[] {
  return ss.map(runScenario);
}

// CLI: read {scenarios:[...]} on stdin, write {results:[...]} on stdout
if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split("/").pop()!)) {
  const chunks: Buffer[] = [];
  process.stdin.on("data", (c) => chunks.push(c));
  process.stdin.on("end", () => {
    const input = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    process.stdout.write(JSON.stringify({ results: runScenarios(input.scenarios) }));
  });
}
