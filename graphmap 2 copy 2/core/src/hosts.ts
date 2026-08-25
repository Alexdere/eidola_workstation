// Hosts — what fills the execution hole.
//
// Nothing in here is core. A host turns an opaque body into something callable, and that is
// the only place a *vocabulary* is allowed to exist. `add` is not a kernel concept; it is
// something someone wrote and a host ran. Swap the host and the same graph runs against a
// different world, which is exactly what a port is for.

import { Value } from "./canon.js";
import { Body, Unit } from "./model.js";
import { ExecutionPort, LeafDef } from "./ports.js";

/**
 * JS host. A body's `src` is JavaScript that RETURNS either
 *   a function        (inp, ctx) => ({ out: … })        — pure · source · effect
 *   or an object      { cook, read, write, act }        — anything, and required for delay
 *
 * The source is the unit's content, so its hash covers it: change a character and it is a
 * different unit. That is Law 3 doing its job, not a limitation to route around.
 */
export class JsHost implements ExecutionPort {
  readonly kind = "js";
  private cache = new Map<string, LeafDef>();

  resolve(body: Body, unit: Unit): LeafDef {
    if (body["@"] !== "host/1") throw new Error(`JsHost cannot run ${body["@"]}`);
    if (body.lang !== "js") throw new Error(`JsHost cannot run lang "${body.lang}"`);
    const key = body.src;
    const hit = this.cache.get(key);
    if (hit) return hit;

    let made: unknown;
    try {
      made = new Function(`"use strict";\n${body.src}`)();
    } catch (e) {
      throw new Error(`body failed to compile: ${(e as Error).message}`);
    }
    let def: LeafDef;
    if (typeof made === "function") {
      const f = made as (i: Record<string, Value>, c: never) => Record<string, Value>;
      def = unit.cls === "effect"
        ? { act: (i, c) => { f(i, c as never); } }
        : { cook: (i, c) => f(i, c as never) ?? {} };
    } else if (made && typeof made === "object") {
      def = made as LeafDef;
    } else {
      throw new Error("a body must return a function or an object");
    }

    const want = unit.cls === "delay" ? ["read", "write"] : unit.cls === "effect" ? ["act"] : ["cook"];
    for (const k of want)
      if (typeof (def as Record<string, unknown>)[k] !== "function")
        throw new Error(`a ${unit.cls} body must provide ${want.join(" and ")}`);

    this.cache.set(key, def);
    return def;
  }
}

/** Try each host in turn. The first that can run the body, does. */
export class Hosts implements ExecutionPort {
  readonly kind = "hosts";
  constructor(private hosts: ExecutionPort[]) {}
  add(h: ExecutionPort): this { this.hosts.push(h); return this; }
  resolve(body: Body, unit: Unit): LeafDef {
    let last: Error | null = null;
    for (const h of this.hosts) {
      try { return h.resolve(body, unit); } catch (e) { last = e as Error; }
    }
    throw last ?? new Error(`no host can run ${body["@"]}`);
  }
}
