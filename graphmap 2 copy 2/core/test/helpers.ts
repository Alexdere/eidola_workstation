import { Store } from "../src/store.js";
import { MemoryPersistence } from "../src/ports.js";
import { primUnit } from "../src/prims.js";
import { Graph, Pane, Port, Unit, Value, container, graph, pane, port, wire } from "../src/index.js";

export function ws() {
  const store = new Store(new MemoryPersistence());
  const u = (op: string) => store.putUnit(primUnit(op));
  const put = (unit: Unit) => store.putUnit(unit);
  return { store, u, put };
}

/** Same op, same computation — but the boundary declares frames. Frame is a property of the
 *  seam, not of the recipe, so this is a different unit with the same behaviour. */
export function framed(op: string, frames: Record<string, string>): Unit {
  const base = primUnit(op);
  return {
    ...base,
    ports: base.ports.map((p) =>
      p.name in frames ? ({ ...p, frame: frames[p.name] } as Port) : p,
    ),
  };
}

export const g = graph;
export const p = pane;
export const w = wire;
export { container, port, wire, pane, graph };
export type { Graph, Pane, Value };
