// Flattening: a nested graph becomes a set of leaf sites addressed by PATH, plus the
// resolved producer of every consumer port.
//
// This is where "an address is a path, not an id" becomes mechanical. One unit appearing in
// forty places is one unit and forty paths — and because the memo key is content while the
// address is not, those forty paths share one memo entry.
//
// Containers do not disappear: their boundary ports survive as RELAY endpoints, so a value
// crossing into a composite crosses a real seam rather than being inlined away.

import { Graph, Pane, Port, Unit, IN, OCC_RE, OUT, findPort } from "./model.js";
import { Store } from "./store.js";
import { FrameRegistry, Transition } from "./frames.js";

export interface Site {
  path: string;
  unit: Unit;
  pane: Pane;
}

export interface Link {
  from: string; // EP
  to: string;   // EP
}

export interface Resolved {
  producer: string;          // leaf out EP
  route: Transition[];       // frame conversions, producer -> consumer order
}

export interface Flat {
  sites: Map<string, Site>;          // leaves only, by path
  containers: Map<string, Unit>;     // by path
  epPort: Map<string, Port>;         // EP -> its port declaration
  links: Link[];
  srcOf: Map<string, Link>;          // consumer-side EP -> the link feeding it
  resolved: Map<string, Resolved>;   // leaf in EP -> producing leaf out EP (+ conversions)
  order: string[];                   // topological order of leaf paths for pass B
  /** container paths whose pane is pinned. A pin is a declaration ON A SEAM that what is
   *  behind it has a reason to run — so it must reach the interior, or pinning a composite
   *  would be a no-op and Law 6 would have a hole exactly where composites are. */
  pinnedPrefixes: string[];
}

export class GraphError extends Error {}

export const ep = (path: string, port: string) => `${path}#${port}`;
export const epPath = (e: string) => e.slice(0, e.lastIndexOf("#"));
export const epPortName = (e: string) => e.slice(e.lastIndexOf("#") + 1);

function joinPath(prefix: string, occ: string) {
  return prefix ? `${prefix}/${occ}` : occ;
}

function endpointEp(prefix: string, [occ, portName]: [string, string]): string {
  // @in / @out address the CONTAINER ITSELF — the boundary appears in the graph as two
  // ordinary endpoints, which is the only form that can express fan-out (D1).
  if (occ === IN || occ === OUT) return ep(prefix, portName);
  return ep(joinPath(prefix, occ), portName);
}

export function flatten(root: Graph, store: Store, frames: FrameRegistry): Flat {
  const f: Flat = {
    sites: new Map(), containers: new Map(), epPort: new Map(),
    links: [], srcOf: new Map(), resolved: new Map(), order: [], pinnedPrefixes: [],
  };

  const walk = (g: Graph, prefix: string, depth: number) => {
    if (depth > 64) throw new GraphError("graph nested too deeply");

    for (const occ of Object.keys(g.panes)) {
      if (!OCC_RE.test(occ)) throw new GraphError(`bad occurrence name: ${occ}`);
      if (occ === IN || occ === OUT) throw new GraphError(`${occ} is reserved`);
      const pane = g.panes[occ];
      const path = joinPath(prefix, occ);
      const u = store.unit(pane.unit);

      for (const p of u.ports) f.epPort.set(ep(path, p.name), p);

      if (u.cls === "container") {
        if (u.powers.length) throw new GraphError(`container ${path} declares powers`);
        if (!u.graph) throw new GraphError(`container ${path} has no interior`);
        if (pane.mem !== null) throw new GraphError(`container ${path} names a mem address`);
        f.containers.set(path, u);
        if (pane.pinned) f.pinnedPrefixes.push(path);
        walk(u.graph, path, depth + 1);
      } else {
        if (!u.body) throw new GraphError(`leaf ${path} has no body`);
        const wantsMem = u.powers.some((p) => p.startsWith("mem:"));
        if (wantsMem !== (pane.mem !== null))
          throw new GraphError(
            `${path}: mem address ${pane.mem === null ? "missing" : "given"} but unit ` +
              `${wantsMem ? "declares" : "declares no"} mem power`,
          );
        f.sites.set(path, { path, unit: u, pane });
      }
    }

    for (const w of g.wires) {
      for (const [occ] of [w.from, w.to]) {
        if (occ === IN || occ === OUT) {
          if (!prefix) throw new GraphError(`${occ} used in the root graph, which has no boundary`);
        } else if (!(occ in g.panes)) {
          throw new GraphError(`wire endpoint names no pane: ${occ}`);
        }
      }
      checkEndpoint(g, prefix, w.from, "from");
      checkEndpoint(g, prefix, w.to, "to");
      f.links.push({ from: endpointEp(prefix, w.from), to: endpointEp(prefix, w.to) });
    }
  };

  const checkEndpoint = (
    g: Graph, prefix: string, [occ, portName]: [string, string], side: "from" | "to",
  ) => {
    // Inside a composite the polarity flips: @in SUPPLIES the interior (so it acts as a
    // producer) while being an `in` port of the boundary.
    let u: Unit, wantDir: "in" | "out";
    if (occ === IN || occ === OUT) {
      u = store.unit(ownerUnitOf(prefix));
      wantDir = occ === IN ? "in" : "out";
      if (occ === IN && side !== "from") throw new GraphError("@in may only be a wire source");
      if (occ === OUT && side !== "to") throw new GraphError("@out may only be a wire target");
    } else {
      u = store.unit(g.panes[occ].unit);
      wantDir = side === "from" ? "out" : "in";
    }
    const p = findPort(u, portName);
    if (!p) throw new GraphError(`no port ${portName} on ${occ}`);
    if (p.dir !== wantDir)
      throw new GraphError(`port ${occ}.${portName} is ${p.dir}, expected ${wantDir}`);
  };

  // the unit that owns the graph currently being walked, so @in/@out can be typed
  const owners = new Map<string, string>();
  const seed = (g: Graph, prefix: string) => {
    for (const occ of Object.keys(g.panes)) {
      const pane = g.panes[occ];
      const path = joinPath(prefix, occ);
      const u = store.unit(pane.unit);
      if (u.cls === "container" && u.graph) {
        owners.set(path, pane.unit);
        for (const p of u.ports) f.epPort.set(ep(path, p.name), p);
        seed(u.graph, path);
      }
    }
  };
  const ownerUnitOf = (prefix: string) => {
    const id = owners.get(prefix);
    if (!id) throw new GraphError(`no owning composite for ${prefix}`);
    return id;
  };

  seed(root, "");
  walk(root, "", 0);

  // --- fan-in is not a thing; fan-out is
  for (const l of f.links) {
    if (f.srcOf.has(l.to)) throw new GraphError(`two wires into ${l.to}`);
    f.srcOf.set(l.to, l);
  }

  // --- shapes and frames, checked at wire time
  for (const l of f.links) {
    const a = f.epPort.get(l.from);
    const b = f.epPort.get(l.to);
    if (!a || !b) throw new GraphError(`wire endpoint has no port: ${l.from} -> ${l.to}`);
    if (a.shape !== "any" && b.shape !== "any" && a.shape !== b.shape)
      throw new GraphError(`shape mismatch ${l.from}:${a.shape} -> ${l.to}:${b.shape}`);
    if (!frames.connectable(a.frame, b.frame))
      throw new GraphError(`no frame route ${a.frame} -> ${b.frame} (${l.from} -> ${l.to})`);
  }

  // --- resolve every leaf input to the leaf output that actually produces it
  for (const site of f.sites.values()) {
    for (const p of site.unit.ports) {
      if (p.dir !== "in") continue;
      const consumer = ep(site.path, p.name);
      let hop = f.srcOf.get(consumer);
      const route: Transition[] = [];
      let guard = 0;
      while (hop) {
        if (++guard > 4096) throw new GraphError(`cyclic wiring at ${consumer}`);
        const a = f.epPort.get(hop.from)!;
        const b = f.epPort.get(hop.to)!;
        if (a.frame !== b.frame && a.frame && b.frame)
          route.unshift(...frames.canonicalRoute(a.frame, b.frame));
        const fromPath = epPath(hop.from);
        const fromSite = f.sites.get(fromPath);
        if (fromSite && findPort(fromSite.unit, epPortName(hop.from))?.dir === "out") {
          f.resolved.set(consumer, { producer: hop.from, route });
          break;
        }
        hop = f.srcOf.get(hop.from); // a relay; keep walking
      }
      // no producer -> the input is absent. Absence is a member of the set, not an error.
    }
  }

  f.order = topoOrder(f);
  return f;
}

/**
 * Topological order over leaf sites for pass B. Edges incident to `delay` sites are cut:
 * a delay's output was produced in pass A and its input is consumed in pass C, so inside
 * one tick the graph is always a DAG. A cycle without a delay is refused HERE — never
 * discovered mid-tick.
 */
function topoOrder(f: Flat): string[] {
  const deps = new Map<string, Set<string>>();
  for (const path of f.sites.keys()) deps.set(path, new Set());
  for (const [consumerEp, r] of f.resolved) {
    const consumer = epPath(consumerEp);
    const producer = epPath(r.producer);
    if (f.sites.get(consumer)!.unit.cls === "delay") continue; // pass C
    if (f.sites.get(producer)!.unit.cls === "delay") continue; // pass A
    deps.get(consumer)!.add(producer);
  }

  const out: string[] = [];
  const state = new Map<string, 0 | 1 | 2>();
  const visit = (p: string, stack: string[]) => {
    const s = state.get(p) ?? 0;
    if (s === 2) return;
    if (s === 1)
      throw new GraphError(
        `cycle without a delay: ${[...stack.slice(stack.indexOf(p)), p].join(" -> ")}`,
      );
    state.set(p, 1);
    stack.push(p);
    for (const d of [...deps.get(p)!].sort()) visit(d, stack);
    stack.pop();
    state.set(p, 2);
    out.push(p);
  };
  for (const p of [...f.sites.keys()].sort()) visit(p, []);
  return out;
}
