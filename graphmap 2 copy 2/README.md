# graphmap — spec, core, oracle

The base the head gets built on. Three things, in dependency order:

```
spec/     CANON.md      CVE/1 — the canonical value encoding and hash
          CORE-FORMAT.md CORE/1 — units, panes, wires, sigma, the five-pass tick
          vectors/      golden vectors both implementations must reproduce

core/     TypeScript. The T0 kernel — runs in a browser tab, no dependencies,
          no Web Crypto, no DOM. 41 falsification tests, liminal space included.

head/     eidola — the browser head. Open panes, wire them by dragging port to
          port, group a selection into a new unit, descend into it. Bundles to
          one self-contained HTML file. 22 checks driven in a real browser.

oracle/   Python. An INDEPENDENT implementation of the same spec, plus the grader
          that runs both over generated workspaces and compares sigma per tick.
```

## Run it

```
./check.sh                     # everything
N=1000 SEED=42 ./check.sh      # a longer fuzz run
```

## Why an oracle

Identity is content, and sigma is a map of hashes, so two independent implementations must
tick to **bit-identical sigma** or one of them is wrong. That is the Hermione race applied to
the kernel itself: the deterministic implementation grades the other one forever, and shadow
mode is free because truth is append-only.

Grading two implementations against each other is also what **forces the serialization spec
to exist first** — you cannot compare two cores over a format that either of them owns. So
the grader and the versioned workspace file are the same artifact, which is how a
near-impossible-to-retrofit requirement got paid for early instead of late.

## What the build found

Five things the design did not say, in the order they were forced:

1. **Code is a blob, so the same computation in two languages is two units.** Cross-language
   identity is therefore impossible for host code — correctly, by Law 3. The fix is a
   **primitive table** whose identity is the op name: a shared ground both cores implement.
   Host bodies stay host extensions, graded within a language only.

2. **Route selection had to become part of the spec.** Frame transitions agree only within a
   tolerance, so *which* route a value takes is visible in the last bits of sigma. Selection
   by registration order would have made two conformant cores disagree. The canonical route
   is now the shortest, ties broken lexicographically — a property of the registry's content,
   never of how it was built.

3. **Route agreement needs a declared tolerance.** Requiring bit-identical agreement between
   `c→k` and `c→f→k` would make finding #5's check either vacuous or permanently red, and a
   permanently red check gets switched off. The tolerance belongs in the frame declaration.

4. **"Integers beyond ±(2⁵³−1) are not values" was wrong**, and the fuzzer proved it: it made
   a core refuse `1e10 × 1e10`, a product of its own values. In a one-numeric-type universe
   there are no integer types to bound. The exactness rule belongs on the *encoder's input*,
   in whichever language has an exact integer type — Python does, JavaScript does not.

5. **Gathering inputs must be all-or-nothing.** Writing each input into sigma as it is found
   makes a site with one missing input contribute a partial, port-declaration-order-dependent
   fragment. Absence is a member of the set; a partial absence is not.

Items 2 and 4 were found by the differential fuzzer, not by either test suite. Neither
implementation was wrong on its own terms — the *spec* was underdetermined, which is exactly
the find the grader exists to produce.

## The head, in one screen

Open `head/dist/eidola.html`. What is worth doing first, in order:

1. **Drag a pane.** Watch the graph hash in the top bar. It does not move, and the counter
   beside it says how many gauge operations have happened since it last did. That is Law 8,
   watchable rather than asserted.
2. **Fold a pane** (the `–` in its header). Still no hash change — but the value behind it
   goes away, because folded means unobserved and unobserved means not alive. A purely
   presentational act, changing what runs without changing what the thing is.
3. **Drag port to port.** Now the hash moves, because a wire is content. Try wiring a `bool`
   out to a `num` in: it is refused at wire time and says why.
4. **Select two panes and press group.** They become one unit, and the boundary is not
   invented — it is exactly the wires that already crossed your selection. The new unit
   appears in the palette, usable anywhere any unit is usable.
5. **Double-click it to descend.** The hash readout says *liminal*: you are editing a draft
   that has no identity yet. Add panes, play it, and nothing is stored. Press
   **declare & ascend** and the hash exists.

## What is deliberately not here
- **A real ExecutionPort.** The local host just calls the function, so Novelty is enforced by
  convention rather than by construction. A worker host is what makes it impossible.
- **Incremental folds.** `mem_fold` recomputes over the whole log. Correct, and wrong at
  scale.
- **Higher-order units.** A port carrying a unit id is not implemented.
- **Frame-relative time.** No per-object local oscillator against a master tick. Still the one
  place the core is genuinely open.
