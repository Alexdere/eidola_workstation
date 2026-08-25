# CVE/1 — Canonical Value Encoding

*Version 1. The byte-level ground both cores stand on.*

Every identity in this system is `sha256` over a byte string. Two implementations agree on
identity only if they agree on those bytes. This document defines them exactly.

The rule that shaped it: **remove, from the value universe, every construct where two
languages could reasonably disagree.** What survives is small, prefix-free, and trivially
identical in TypeScript and Python.

---

## 1. The value universe

Seven types. Nothing else is a value.

| type | notes |
|---|---|
| `null` | |
| `bool` | |
| `num` | IEEE 754 binary64. **The only numeric type.** |
| `str` | Unicode text |
| `bytes` | opaque octets |
| `list` | ordered, heterogeneous |
| `map` | **string keys only**, unordered (order is not content) |

### Why one numeric type

In JavaScript `1` and `1.0` are the same value; in Python they are `int` and `float`. Any
universe containing both an integer and a float type must therefore choose a rule for which
one `1` is, and the two languages will disagree at some edge. So the universe contains one
numeric type, binary64, and `1` and `1.0` **are the same value** — which is the truth in
one of the two languages and a deliberate narrowing in the other.

Consequence, stated rather than hidden: **every binary64 is a value, and nothing else is a
number.** `1e20` and `1e300` are ordinary values — they are exactly representable doubles, and
a core that computes one (`1e10 × 1e10`) must be able to encode it.

The asymmetry this creates is the whole rule, and it is not symmetric between the two
languages:

- A language whose only numeric type is binary64 (JavaScript) has **nothing to check**.
- A language with an exact integer type (Python's `int`, a `BigInt`) must convert it exactly
  or **refuse** — `float(n)` must round-trip. `2⁵³+1` is refused; `10²⁰` is accepted, because
  it survives the trip.

*This paragraph is the correction the fuzzer forced.* The first version of this spec said
"integers beyond ±(2⁵³−1) are not values," which reads sensibly and is wrong: it describes a
property of integer *types*, and in a one-numeric-type universe there are none. Implemented
literally it made a core refuse the product of two of its own values. The rule belongs on the
encoder's input, not on the universe.

Counts and lengths *inside the encoding* are not values and are not subject to any of this.

### Numeric canonicalisation

Two binary64 bit patterns must be normalised before encoding, or equal values would hash
differently:

- `-0.0` encodes as `+0.0` (bits `00000000_00000000`)
- every `NaN` encodes as the canonical quiet NaN, bits `7ff8000000000000`

`+Infinity` and `-Infinity` are ordinary values and encode as their bit patterns.

### Map keys

Keys are `str`. Encoding sorts them **ascending by their UTF-8 byte sequence** — not by code
point, not by UTF-16 code unit (which is what RFC 8785 chose, and which is the single most
common source of cross-language canonicalisation bugs). Byte-sequence ordering is the same
comparison in every language that has a byte string.

Duplicate keys cannot occur; an implementation encountering them MUST error.

---

## 2. The encoding

`enc(v) -> bytes`. Prefix-free and unambiguously decodable without lookahead.

```
null          z
false         f
true          t
num  x        d  <8 bytes, IEEE754 binary64, big-endian>
str  s        s  dec(len(utf8(s)))  ':'  utf8(s)
bytes b       x  dec(len(b))        ':'  b
list [a,b,…]  l  dec(count)         ';'  enc(a) enc(b) …
map  {…}      m  dec(count)         ';'  ( key(k) enc(v) )…      k ascending by utf8 bytes
```

where

```
key(k)   =  'k' dec(len(utf8(k))) ':' utf8(k)
dec(n)   =  ASCII decimal, no leading zeros, no sign. dec(0) = "0".
```

`dec` is applied only to non-negative counts and byte lengths that belong to the *encoding*,
never to a value.

Every tag byte is a distinct ASCII letter, so a hexdump of any encoding is readable, which
matters more than compactness for a format whose job is to be agreed upon.

### Worked example

```
{"a": 1, "b": [true, null]}

m 2 ;
  k1:a  d 3ff0000000000000
  k1:b  l 2 ; t z
```

as bytes:

```
6d 32 3b 6b 31 3a 61 64 3f f0 00 00 00 00 00 00 6b 31 3a 62 6c 32 3b 74 7a
m  2  ;  k  1  :  a  d  ·  ·  ·  ·  ·  ·  ·  ·  k  1  :  b  l  2  ;  t  z
```

---

## 3. Hashing

```
H(v) = sha256( DOMAIN || enc(v) )       DOMAIN = b"graphmap/cve1\x00"
```

rendered lowercase hex.

The domain tag means a `graphmap` hash can never coincide with a hash of the same bytes taken
for any other purpose. It costs 14 bytes and removes a whole class of cross-protocol
confusion.

### Typed identifiers

Structural objects carry a `"@"` field naming their type and version (`"unit/1"`,
`"graph/1"`, …), so two different kinds of object can never collide even if their remaining
fields coincide. Identifiers are rendered with a readable prefix:

```
blob:<64 hex>      an immutable value
unit:<64 hex>      a unit (a seam: boundary + interior)
```

The prefix is presentation. `H` is computed over the encoding, never over the rendered string.

---

## 4. What an implementation must reject

Silence here is how two cores drift apart, so these are errors, not coercions:

1. a native exact integer that does not survive the round-trip through a binary64
2. a map key that is not a `str`
3. duplicate map keys
4. any value of a type outside the seven
5. a cyclic value structure (the encoder must not loop; depth is finite by construction)

---

## 5. Conformance

`spec/vectors/canon.json` holds the golden vectors: for each, the value, its encoding in hex,
and its hash. **An implementation is conformant when it reproduces every hex string exactly.**
Both `core/` (TypeScript) and `oracle/` (Python) run this file as a test.

The vectors deliberately include the cases where languages disagree: `-0.0`, `NaN`, `±∞`,
non-BMP text, keys whose UTF-8 order differs from their UTF-16 order, the empty map, the
empty list, `2⁵³−1` at the exact-integer boundary, and `10²⁰` just past it — which is a value,
not an error.
