// VSPEC — a JSON-safe description of a value.
//
// JSON cannot represent NaN, -0, or bytes, and the whole point of the vectors is to pin
// exactly those. So the interchange files describe values rather than containing them, and
// both cores decode the description into their own native value.

import { Value, isMap } from "./canon.js";

export type VSpec =
  | { n: null } | { b: boolean } | { d: string } | { s: string }
  | { x: string } | { l: VSpec[] } | { m: Record<string, VSpec> };

const hexToBytes = (h: string) => {
  const out = new Uint8Array(h.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(h.slice(i * 2, i * 2 + 2), 16);
  return out;
};
const bytesToHex = (b: Uint8Array) =>
  [...b].map((x) => x.toString(16).padStart(2, "0")).join("");

export function fromSpec(s: VSpec): Value {
  if ("n" in s) return null;
  if ("b" in s) return s.b;
  if ("d" in s) {
    const dv = new DataView(new ArrayBuffer(8));
    dv.setBigUint64(0, BigInt("0x" + s.d));
    return dv.getFloat64(0);
  }
  if ("s" in s) return s.s;
  if ("x" in s) return hexToBytes(s.x);
  if ("l" in s) return s.l.map(fromSpec);
  if ("m" in s) {
    const o: Record<string, Value> = {};
    for (const k of Object.keys(s.m)) o[k] = fromSpec(s.m[k]);
    return o;
  }
  throw new Error("bad vspec: " + JSON.stringify(s));
}

export function toSpec(v: Value): VSpec {
  if (v === null) return { n: null };
  if (typeof v === "boolean") return { b: v };
  if (typeof v === "number") {
    const dv = new DataView(new ArrayBuffer(8));
    if (Number.isNaN(v)) dv.setBigUint64(0, 0x7ff8000000000000n);
    else dv.setFloat64(0, Object.is(v, -0) ? 0 : v);
    return { d: bytesToHex(new Uint8Array(dv.buffer)) };
  }
  if (typeof v === "string") return { s: v };
  if (v instanceof Uint8Array) return { x: bytesToHex(v) };
  if (Array.isArray(v)) return { l: v.map(toSpec) };
  if (isMap(v)) {
    const m: Record<string, VSpec> = {};
    for (const k of Object.keys(v)) m[k] = toSpec(v[k]);
    return { m };
  }
  throw new Error("not a value");
}
