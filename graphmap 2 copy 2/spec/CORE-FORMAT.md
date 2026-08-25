# CORE/1 — objects, identity, and the tick

*Version 1. What a unit is, how it is named, and exactly what a tick does.*

Built on `CANON.md` (CVE/1). Everything here is a value in that universe, so everything here
has a hash. Implements the eight laws of `claude/the-generator.md`.

---

## 1. Objects

### Port

```
{ "@": "port/1",
  "name":  str,
  "dir":   "in" | "out",
  "shape": str,            # what it carries structurally
  "frame": str | null }    # what its values mean (Law 7)
```

Shape `"trigger"` carries intent rather than a value; there is no separate wire kind
(`implementation-v0.1.md` D2 — what a wire carries is a property of the ports it joins).

### Unit — the seam

```
{ "@": "unit/1",
  "ports":  [ Port, … ],   # ORDERED — a boundary is an ordered interface
  "powers": [ str, … ],    # SORTED   — a set, not a sequence
  "cls":    "pure" | "source" | "delay" | "effect" | "container",
  "body":   Body | null,   # exactly one of body / graph is non-null
  "graph":  Graph | null }
```

`unit:H(unit)`.

**Exactly two interior kinds** (Law 2). `body` is an opaque leaf; `graph` is a graph of units.
A third would be a new construct, and the law forbids it.

`ports` is ordered and `powers` is sorted, and the asymmetry is the point: reordering an
interface changes it, whereas the set of powers a seam declares has no order to change
(`implementation-v0.1.md` D3).

### Body — two kinds, and only one of them crosses languages

```
{ "@": "prim/1", "op": str }                     # portable
{ "@": "host/1", "lang": str, "src": str }       # not portable
```

A primitive's identity is its **op name**, drawn from the table in §6, which every conformant
core implements identically. A host body's identity is its **source text**, which is
JavaScript in one core and Python in another.

> The same computation written in two languages is two different contents, therefore two
> different units, therefore two different hashes. That is Law 3 working correctly, not a
> defect. Cross-language grading is defined over primitive bodies; host bodies are host
> extensions and are graded within a language only.

This was the first thing the build found that the design had not said. It is why the spec
carries a primitive table at all.

### Pane — a binding site

```
{ "@": "pane/1",
  "unit":   "unit:<hex>",
  "config": "blob:<hex>" | null,
  "mem":    str | null,        # memory address, bound to the unit's declared role
  "pinned": bool }
```

Which unit, with what settings, whose state. **Not** position, **not** cached values
(`core-spec.md` §1). `pinned` is a *declaration* that this binding has a reason to be alive,
so it is content. `observed` is not: it is a per-tick input from a head, and lives outside the
graph (Law 8).

### Wire

```
{ "@": "wire/1", "from": [occ, port], "to": [occ, port] }
```

### Graph

```
{ "@": "graph/1",
  "panes": { occ: Pane, … },
  "wires": [ Wire, … ] }      # SORTED by canonical encoding before hashing
```

Occurrence names match `[A-Za-z0-9_.-]+` and may not contain `/`.

The reserved occurrences **`@in`** and **`@out`** are a composite's own boundary ports,
addressable inside its graph. There is no separate `expose` table
(`implementation-v0.1.md` D1) — the boundary appears in the graph as two ordinary endpoints,
which is the only form that can express fan-out.

Wires are sorted before hashing so authoring order cannot enter identity; this is a Law 8
boundary case and is exactly where a byte-identical test earns its keep.

---

## 2. Well-formedness — refused at wire time, not discovered at run time

A graph is rejected unless all hold. Each is a law made unrepresentable rather than
detectable.

1. every wire endpoint names an existing pane, or `@in` / `@out`
2. every named port exists on that pane's unit with the right direction
   (`@in` supplies the *container's* `in` ports; `@out` consumes its `out` ports)
3. no `in` port has two incoming wires (fan-in is not a thing; fan-out is)
4. shapes match across a wire
5. frames match, or a transition is declared in the frame registry (Law 7)
6. **the graph, with delay units removed, is acyclic** — a cycle is refused here, never
   discovered mid-tick (`core-spec.md` §3e)
7. no container unit declares a power (Law 4: no composite may bind a power)
8. a pane's `mem` is non-null iff its unit declares a `mem:*` power (Law 4: naming an address
   not on the boundary is `PowerDenied`)

---

## 3. State: four lifetimes

```
G   graph + units + code       immutable, content-addressed     forever
B   blobs (values)             immutable, content-addressed     forever
σ   PortAddr -> BlobId         the current values               one tick
M   mem: addr -> [BlobId]      append-only logs                 the run
```

Nothing is mutated. The only thing that changes is which immutable objects are referenced.

**A PortAddr is a path, not an id**: `"outer/inner/port"`, occurrence names joined by `/`.
One unit appearing in forty places is one unit and forty paths — which is exactly what lets
forty occurrences share a single memo entry, because the memo key is content and the address
is not.

### σ, canonically

σ is a `map` from PortAddr to BlobId, so σ has a hash like anything else:

```
sigmaHash = H({ "@": "sigma/1", "tick": num, "at": { path: "blob:<hex>", … } })
```

**This is the graded observable.** Two cores agree iff `sigmaHash` matches at every tick.

---

## 4. The tick — five passes

The five passes are not a rule *about* the runtime; they are its shape
(`implementation-v0.1.md` §3). And the five classes are the five passes — that is the whole
content of the taxonomy:

| pass | | class |
|---|---|---|
| **0** | sources emit; every emission is journalled on the way in | `source` |
| **A** | delay reads: each emits its fold over its log *as it stood at tick start* | `delay` |
| **B** | the body, in topological order over the live set, memoised on content | `pure`, `effect` |
| **C** | delay writes: inputs have settled, appends are buffered | `delay` |
| **D** | commit: the logs grow by one tick's worth | — |

`container` is structural and belongs to no pass; it is descended through.

Passes A and C are the cut. **Inside one tick the graph is always a DAG**, so there is no
fixpoint to iterate and no paradox to find at run time. Cyclic behaviour is not eliminated —
it is lifted into the tick index. *The universe of forms is a DAG; the universe of events is
a cyclic graph indexed by time.*

> **A delay's read phase is handed no inputs at all.** Not "should not use them" — cannot see
> them. Its read function's signature has no input parameter. Impossible beats detectable, and
> this is the place where writing the weaker version is most tempting.

### Liveness

```
live(p)  ←  observed(p) ∨ pinned(p) ∨ ∃ live consumer downstream
dirty(p) ←  p never ran, or an input blob id differs from its last run
run(p)   when  live(p) ∧ dirty(p)
```

Liveness propagates **upstream**: a source feeding a watched pane stays live. The viewport is
a garbage collector for computation.

An `effect` unit has no `out` port, so nothing can ever be downstream of it, so it is dead
forever unless `pinned`. That is not a bug — it is Law 6 saying **a world-facing act needs a
declared reason to be alive.**

### Memoisation

Key: `H({"@":"memo/1", "unit":…, "config":…, "inputs":{port: blobId,…}})`.

Applied **only to pure units**. Purity is a structural property computed bottom-up over unit
ids: a leaf is pure iff `cls == "pure"`; a container is pure iff every unit it binds is pure.
The memo table is cache tier — it is deterministically rebuildable and is **deliberately not
shipped** by migration. If anything lived outside (G, B, σ, M), migration would lose it and
the test would say so.

### Sources and replay

A source does not read the world during a tick. It receives its emission from the **source
journal** for that tick:

```
journal = [ { tick: num, at: { "path/to/source/port": value, … } }, … ]
```

Recording the journal and replaying it reproduces every σ exactly. This is what "open with
enumerable openings" buys: the boundary is a finite printable list.

---

## 5. Powers and the floor

A unit's execution context carries **exactly the powers its boundary declares** and nothing
else. Reaching for an undeclared power raises `PowerDenied` at the point of the reach.

```
mem:r     read own memory log      delay
mem:w     append to own log        delay
emit      produce a source value   source
world     perform an effect        effect
```

**There is no `now`.** Time is not a power; a clock is a source or it does not exist (Law 5).

Memory binds **by role, not by address** (`implementation-v0.1.md` §2): the leaf names a role
(`mem:r`), the pane names the address, the runtime binds one to the other at cook time. So one
`delay` unit serves every delay in the system, and changing a binding's state scope changes
its identity without touching a character of its body.

---

## 6. The primitive table

The shared ground. Identity is the op name; every conformant core implements these
identically. Ports are listed in boundary order.

| op | cls | in | out | meaning |
|---|---|---|---|---|
| `core.const` | pure | — | `out:any` | emits `config.value` |
| `core.add` | pure | `a:num b:num` | `out:num` | a + b |
| `core.sub` | pure | `a:num b:num` | `out:num` | a − b |
| `core.mul` | pure | `a:num b:num` | `out:num` | a × b |
| `core.neg` | pure | `a:num` | `out:num` | −a |
| `core.gt` | pure | `a:num b:num` | `out:bool` | a > b |
| `core.eq` | pure | `a:any b:any` | `out:bool` | canonical-encoding equality |
| `core.not` | pure | `a:bool` | `out:bool` | ¬a |
| `core.and` | pure | `a:bool b:bool` | `out:bool` | a ∧ b |
| `core.or` | pure | `a:bool b:bool` | `out:bool` | a ∨ b |
| `core.if` | pure | `c:bool a:any b:any` | `out:any` | c ? a : b |
| `core.concat` | pure | `a:str b:str` | `out:str` | a ++ b; a non-string operand contributes `""` |
| `core.list2` | pure | `a:any b:any` | `out:list` | [a, b] |
| `core.source` | source | — | `out:any` | emits the journal entry for this tick |
| `core.delay` | delay | `x:any` | `out:any` | last value written, else `config.init` |
| `core.sum` | delay | `x:num` | `out:num` | running sum of writes, from `config.init` |
| `core.lt` | pure | `a:num b:num` | `out:bool` | a < b |
| `core.abs` | pure | `a:num` | `out:num` | \|a\| |
| `core.min` | pure | `a:num b:num` | `out:num` | NaN if either is NaN, else the smaller |
| `core.max` | pure | `a:num b:num` | `out:num` | NaN if either is NaN, else the larger |
| `core.floor` | pure | `a:num` | `out:num` | ⌊a⌋; NaN and ±∞ pass through unchanged |
| `core.len` | pure | `a:list` | `out:num` | list length; NaN for anything else |
| `core.at` | pure | `a:list i:num` | `out:any` | `a[⌊i⌋]`, or `null` out of range |
| `core.append` | pure | `a:list x:any` | `out:list` | a ++ [x] |
| `core.hist` | delay | `x:any` | `out:list` | the whole log, as a value |
| `core.print` | effect | `x:any` | — | appends to the effect journal |

`min`/`max`/`floor`/`len` are spelled out rather than delegated to a language builtin, because
the builtins disagree: `Math.min(NaN, x)` is NaN while Python's `min()` returns whichever
argument came first; `math.floor` raises on NaN and returns an exact `int`; JavaScript string
length counts UTF-16 units where Python counts code points — which is why `core.len` is
lists-only, since "the length of a string" is not one concept.

`core.hist` is a delay whose fold is the identity. It is what makes a chart of history an
ordinary pattern rather than a feature.

Arithmetic is binary64 throughout, so it is IEEE arithmetic in both languages with no
divergence to negotiate. Division is deliberately absent from v1: it is the one binary op
whose edge cases (`0/0`, `1/0`) invite each language's own opinion, and nothing here needs it
yet.

`core.eq` compares **canonical encodings**, not language-native equality — so `1` equals
`1.0`, and `NaN` equals `NaN`, both because canonicalisation says so.

### Frames

The registry maps `(from, to) -> transition`. A wire whose ports carry different frames needs
a declared transition; a route of several transitions composes.

**Route agreement is checked, not assumed.** Where two frames are joined by more than one
route, all routes must agree on fixtures, or the registry is rejected. This is
`core-spec.md`'s finding #5 — the highest-priority open item — closed structurally before
there are twelve format readers rather than after.

The v1 registry ships `temp.c ↔ temp.f ↔ temp.k`, which has exactly the three-route shape
where a wrong constant shows up as disagreement rather than as a plausible number.

---

## 7. Liminal space

Content addressing has a real cost: **immutability makes editing awkward**, because every
keystroke is a new hash. Liminal space is the answer — mutation is quarantined *outside* the
addressed world.

| stage | mutable | addressed | citable | evaluable |
|---|---|---|---|---|
| **liminal** | yes | no | no | yes, unmemoised |
| **declared** | no | yes | yes | yes, memoised |

Two transitions:

- **melt** — take a declared composite into liminal space. What comes back is a copy with no
  identity; the original is untouched *because it cannot be touched*.
- **declare** — freeze the boundary. The hash exists only now.

A draft is evaluated in a **scratch store that reads through to the real one and writes only
locally**, with the memo off, against the *tentative* boundary: an unwired boundary input is
absent, and a stub may be supplied so the draft can be heard while it is being written. That
last clause is not a nicety — Closure is unusable if you cannot hear it while writing it.

**The boundary is not invented; it is read off the crossings.** Melting a *selection* out of a
graph turns every wire that already crossed the line into a boundary port: an incoming
crossing becomes an `in` port shaped by its consumer, an outgoing crossing becomes an `out`
port shaped by its producer, and several external consumers of one internal port share one
boundary port — fan-out, not fan-in. "A graph you are building is a workspace until you declare
its exposure", and the exposure was already drawn.

### Lineage lives beside the store, never inside identity

`parent_hash` records which unit a declaration was melted from, and it is kept in a ledger
alongside G — **not** as a field on the unit.

> A unit's identity is its content. Two structurally identical units must remain the same
> thing however each of them came to be. Putting lineage inside the content would make
> identical things distinct, which is Law 3 backwards.

Declaration is the one event where lineage belongs, and it is an event the author consciously
performs — which is what the earlier design was missing when it could not find a trigger.

---

## 8. Conformance

An implementation is conformant when it reproduces, byte for byte:

1. `spec/vectors/canon.json` — encodings and hashes of values
2. `spec/vectors/units.json` — unit and graph hashes, including the gauge cases (wires
   reordered → same hash; ports reordered → different hash)
3. `spec/vectors/ticks.json` — for each scenario, `sigmaHash` at every tick

The grader (`oracle/grade.py`) runs both cores over generated workspaces and source journals
and compares σ at every tick. **Any divergence is a bug in a core, or an under-specified spot
in this document — and the second is the more valuable find.**
