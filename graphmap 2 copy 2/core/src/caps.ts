// Law 4, the floor. A context carries exactly the powers its boundary declares.
//
// Note what is absent: there is no `now` for anybody. Time is not a power; a clock is a
// source or it does not exist (Law 5).

import { Value } from "./canon.js";

export class PowerDenied extends Error {
  constructor(power: string) {
    super(`PowerDenied: ${power}`);
  }
}

export type Fold = "last" | "sum" | "all";

export interface Ctx {
  /** mem:r — fold over this binding's log as it stood at the start of the tick. */
  memFold(fold: Fold, init: Value): Value;
  /** mem:w — append to this binding's log. Buffered until pass D. */
  memAppend(v: Value): void;
  /** emit — this source's journalled emission for this tick, or undefined. */
  journal(): Value | undefined;
  /** world — perform an effect. */
  world(v: Value): void;
  /** the binding's config, always available; it is content, not a power. */
  config: Value | null;
}

export interface CtxBacking {
  powers: Set<string>;
  config: Value | null;
  readLog: () => Value[];
  appendLog: (v: Value) => void;
  journalValue: Value | undefined;
  emitWorld: (v: Value) => void;
}

export function makeCtx(b: CtxBacking): Ctx {
  const need = (p: string) => {
    if (!b.powers.has(p)) throw new PowerDenied(p);
  };
  return {
    config: b.config,
    memFold(fold, init) {
      need("mem:r");
      const log = b.readLog();
      if (fold === "last") return log.length ? log[log.length - 1] : init;
      if (fold === "all") return [...log];
      if (fold === "sum") {
        let acc = typeof init === "number" ? init : 0;
        for (const v of log) acc += typeof v === "number" ? v : 0;
        return acc;
      }
      throw new Error(`unknown fold: ${fold}`);
    },
    memAppend(v) {
      need("mem:w");
      b.appendLog(v);
    },
    journal() {
      need("emit");
      return b.journalValue;
    },
    world(v) {
      need("world");
      b.emitWorld(v);
    },
  };
}
