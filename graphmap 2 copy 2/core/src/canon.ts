// CVE/1 — the canonical value encoding. See spec/CANON.md.
//
// This file is the ground the whole system stands on: if two implementations disagree here,
// they disagree about identity, and every downstream agreement is an accident.

import { sha256, toHex } from "./sha256.js";

export type Value =
  | null
  | boolean
  | number
  | string
  | Uint8Array
  | Value[]
  | { [k: string]: Value };

export class CanonError extends Error {}

const DOMAIN = new TextEncoder().encode("graphmap/cve1\0");
const enc = new TextEncoder();

// ---------------------------------------------------------------- byte building

class Buf {
  private parts: Uint8Array[] = [];
  private len = 0;
  push(b: Uint8Array) { this.parts.push(b); this.len += b.length; }
  byte(c: string) { this.push(new Uint8Array([c.charCodeAt(0)])); }
  ascii(s: string) { this.push(enc.encode(s)); }
  done(): Uint8Array {
    const out = new Uint8Array(this.len);
    let o = 0;
    for (const p of this.parts) { out.set(p, o); o += p.length; }
    return out;
  }
}

/** UTF-8 byte-sequence comparison. Not code-point order, not UTF-16 order. */
function cmpUtf8(a: Uint8Array, b: Uint8Array): number {
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) if (a[i] !== b[i]) return a[i] - b[i];
  return a.length - b.length;
}

export function isMap(v: unknown): v is { [k: string]: Value } {
  return (
    typeof v === "object" && v !== null && !Array.isArray(v) && !(v instanceof Uint8Array)
  );
}

// ---------------------------------------------------------------- the encoder

function write(b: Buf, v: Value, depth: number): void {
  if (depth > 512) throw new CanonError("value nested too deeply (cycle?)");

  if (v === null) return b.byte("z");
  if (v === undefined) throw new CanonError("undefined is not a value");

  switch (typeof v) {
    case "boolean":
      return b.byte(v ? "t" : "f");

    case "number": {
      // Every JS number IS a binary64, so there is nothing to check here. The exactness
      // rule lives on the side that has an exact integer type (see spec/CANON.md §1):
      // Python must refuse an int that does not survive the round-trip through a double.
      // The fuzzer found this: 1e10 * 1e10 is a computed, exactly-representable double,
      // and an over-eager range check refused it.
      b.byte("d");
      const dv = new DataView(new ArrayBuffer(8));
      // canonicalisation: -0 -> +0, any NaN -> the canonical quiet NaN
      if (Number.isNaN(v)) dv.setBigUint64(0, 0x7ff8000000000000n);
      else dv.setFloat64(0, Object.is(v, -0) ? 0 : v);
      return b.push(new Uint8Array(dv.buffer));
    }

    case "string": {
      const u = enc.encode(v);
      b.byte("s"); b.ascii(String(u.length)); b.byte(":");
      return b.push(u);
    }

    case "object":
      break;

    default:
      throw new CanonError(`not a value: ${typeof v}`);
  }

  if (v instanceof Uint8Array) {
    b.byte("x"); b.ascii(String(v.length)); b.byte(":");
    return b.push(v);
  }

  if (Array.isArray(v)) {
    b.byte("l"); b.ascii(String(v.length)); b.byte(";");
    for (const item of v) write(b, item, depth + 1);
    return;
  }

  if (isMap(v)) {
    const keys = Object.keys(v);
    const encoded = keys.map((k) => {
      if (typeof k !== "string") throw new CanonError("map key must be a string");
      return { k, u: enc.encode(k) };
    });
    encoded.sort((x, y) => cmpUtf8(x.u, y.u));
    for (let i = 1; i < encoded.length; i++)
      if (cmpUtf8(encoded[i - 1].u, encoded[i].u) === 0)
        throw new CanonError(`duplicate map key: ${encoded[i].k}`);

    b.byte("m"); b.ascii(String(encoded.length)); b.byte(";");
    for (const { k, u } of encoded) {
      b.byte("k"); b.ascii(String(u.length)); b.byte(":"); b.push(u);
      const child = (v as Record<string, Value>)[k];
      if (child === undefined) throw new CanonError(`key ${k} maps to undefined`);
      write(b, child, depth + 1);
    }
    return;
  }

  throw new CanonError("not a value: unsupported object");
}

/** The canonical byte encoding of a value. */
export function canon(v: Value): Uint8Array {
  const b = new Buf();
  write(b, v, 0);
  return b.done();
}

export function canonHex(v: Value): string {
  return toHex(canon(v));
}

/** H(v) = sha256(DOMAIN || canon(v)), lowercase hex. */
export function hash(v: Value): string {
  const body = canon(v);
  const msg = new Uint8Array(DOMAIN.length + body.length);
  msg.set(DOMAIN);
  msg.set(body, DOMAIN.length);
  return toHex(sha256(msg));
}

/** Canonical equality: two values are the same value iff their encodings are equal. */
export function sameValue(a: Value, b: Value): boolean {
  return canonHex(a) === canonHex(b);
}
