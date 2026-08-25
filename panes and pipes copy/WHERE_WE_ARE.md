# Where we are, and why it's shaped like this

A walkthrough of what's been built, written for someone picking it up cold. Every term gets
defined the first time it shows up. The thread running through it is one question:
**does the structure hold together, or is it just a pile of good ideas?**

We're still toying with this. Nothing here is committed to. The point of building it was to
find out which parts survive contact with a keyboard — and a few didn't.

---

## 1. What the thing is

You open a browser tab. There's a landscape. You type — no box appears first, you just type
— and a word finds you something from a library you've been accumulating for years. Press
enter and it becomes a small live rectangle on the screen: a clock, a weather reading, a note,
a chart of something you're watching. Drag from the edge of one to the edge of another and
they're connected; now the second one is showing something computed from the first.

That's the surface. Underneath, the interesting claim is about **where things live**.

---

## 2. The one rule

Every system like this dies the same way: it accumulates special cases until nobody can
predict what a change will break. The defence is an **invariant** — a rule you promise stays
true no matter what you add later.

Ours:

> **A pane owns nothing except status and presence.**

Three words to unpack, and they're the whole architecture:

**A node** is a small script sitting in a database. It knows how to do exactly one thing —
fetch a URL, format a date, draw a line. It has no idea where it will appear on screen, or
whether it will appear at all. It declares what it takes in and what it gives out, and that's it.

**A pattern** is a saved arrangement: *these particular nodes, this one's output wired into
that one's input, with these settings.* A recipe, not a copy — so the same node can appear in
forty patterns without being duplicated once.

**A pane** is the rectangle you see. It's a **relay**: it carries a node's output to your eyes and
your clicks back down, but it contains no logic. Close it and the node keeps running.

So "owns nothing except status and presence" means the pane knows only *where it sits* and
*whether the thing behind it is working right now*. Everything else is somewhere else.

### Why the rule has to forbid something

A rule that permits everything can't help you decide anything. If every choice you make is
"consistent with the invariant," the invariant is a mirror, not a compass.

So the test for any new idea is: **does this force the pane to start owning something?** If
yes, the idea belongs in the node, the pattern, or the runtime instead. That's a question with
a real answer, which is what makes it useful.

Concretely, in the code, this rule is enforced rather than asserted:

- The kernel has no DOM references — there's a test that greps for them and fails.
- Panes have no geometry. Positions live in a separate `LayoutStore` that the graph literally
  cannot reach from where it sits.
- Moving a pane produces a **byte-identical** serialized graph. There's a test.

That last one sounds fussy and is the single most load-bearing decision in the project.
Because layout isn't semantics, the same arrangement can be open in two windows with two
completely different layouts, both live. If position lived on the pane, that would be
impossible, and so would running the whole thing with no screen at all.

---

## 3. The central decision: when does a node run?

This is where systems in this family live or die, and it took the longest to get right.

You can't have one kind of node. You need three, distinguished only by their relationship to
the outside world:

**pure** — takes inputs, returns outputs, touches nothing else. Same inputs, same answer,
always. Safe to run automatically. Safe to cache.

**source** — reads the outside world on its own schedule. A clock. A weather poll. A live
feed. This is the *only* place change enters the system.

**effect** — writes to the outside world. Sends the email, moves the money, flips the switch.
**Never runs on its own.** It waits for an explicit trigger — a button.

That last one is the only safety policy baked into the entire system, and it's the one you'd
regret not having. Your homepage must not fire webhooks because you opened a tab.

(There's a fourth, **container**, but it's structural rather than behavioural — a pane holding
other panes.)

### Caching, and why purity is worth enforcing

**Memoization** is the trick of not redoing work: hash the inputs into a short fingerprint,
and if you've seen that fingerprint before, hand back the old answer.

This only works if "pure" is actually true. So we enforce it rather than trusting it: a node
declared pure gets a context object with no clock, no network, and no memory — *even if its
manifest asks for them*. There's a test that writes a deliberately sneaky node, asks for
everything, and confirms it receives nothing.

That discipline is annoying to write and it buys a lot. If only sources introduce change, and
everything else is deterministic maths, then **recording what the sources emitted lets you
replay the entire system.** Time-travel debugging on a heterogeneous graph, nearly free.
The journal is already there; the replay isn't built yet.

### The scheduler, in four lines

```
dirty(n) ← n changed, or something upstream produced a new value
live(n)  ← observed(n) ∨ pinned(n) ∨ some live consumer sits downstream of n
cook(n)  when dirty(n) ∧ live(n) ∧ (class(n) = 'pure' ∨ triggered(n))
```

"Observed" means some pane on some screen is currently showing it. Which means **the
viewport is a garbage collector for computation** — scroll away from a corner of your
workspace and it goes quiet. No policy, no config, no code.

But that rule as originally written had a bug that only appeared once it was running. If
liveness stops at the observed pane, then a weather source feeding a visible chart dies the
moment the *source's* own pane scrolls off screen — even though you're still looking at its
output. So liveness had to propagate **upstream**: anything feeding something live is itself
live. That's a test now.

And for the things that must keep running whether or not you're looking — a download, an
unread counter, an accumulating log — a pane can be **pinned**. Two values, one field. It's
the difference between a dashboard and a thing that quietly loses your data.

---

## 4. The evidence the structure is coherent

Here's the actual argument, and it's not "the code is clean." It's that **the same handful of
mechanisms keep producing features nobody built.**

**Nesting needed no new concept.** A "super pane" — a box containing a whole wired
subgraph, exposing clean ports to the outside — is a pane with a `parent` field. No room
table, no container system. The canvas renders a level by filtering on `parent`. Tested three
levels deep, with a value flowing from the innermost node to the outermost port.

**Instance vs reference is one field.** Two panes with different state keys are two
independent instances of the same script. Two panes sharing a state key are two views of one
live thing. Most systems build these as separate features. Here it's one column.

**Overlapping arrangements share work but not memory.** Because the cache
fingerprint is `hash(node, config, inputs)` and state is keyed by the *binding*, two patterns
containing the same node with the same inputs automatically share the computation while
keeping separate state. Overlap became free rather than a conflict to resolve. Tested.

**Views need no view system.** A "chart node" is an identity function carrying a `view: 'chart'`
hint. The kernel never reads that field. Adding a renderer is adding a function to a lookup
table in the UI layer.

**Editing needed no editor.** A pane's editor is a form generated from the node's declared
config fields. So a note's text, a transform's code, and a poll's URL are the same code path —
and any node written later gets an editor for free, including ones that don't exist yet.

**Multi-screen will be free.** The kernel's only knowledge of screens is a set of pane ids
that are currently being watched. Three monitors, two tabs, and a terminal client are just
three callers passing different sets. There is no multi-window feature to build.

None of that was designed in. It fell out. When a decomposition keeps handing you things you
didn't ask for, that's the strongest available evidence it carved reality at the joints.

---

## 5. The newest layer: how a node reaches a service

Three ideas that arrived recently and turned out to be one shape.

**Secrets are a fourth storage tier.** Your GitHub token can't live in `config`, because
config is part of the cache fingerprint and is meant to be readable plain text you can diff. A
token in config is a token in a thousand stored fingerprints. So secrets get their own
encrypted drawer, referenced by name, resolved by the runtime *only at the moment a node
runs* — never entering a hash, a log, or a saved file.

This slots into the **capability** system, which is the mechanism where a node must declare
the powers it wants (`net` for network, `fs:write` to write files) and you approve them at
install. A secret is just one more declared power. A weather node asking for `net` is
reasonable; a weather node asking for `secret:email` is instantly suspect. One declaration
doing three jobs: permission prompt, sandbox configuration, and scheduler hint.

**Flavour is a second axis.** Version is vertical — v1, v2, v3 of the same script improving.
**Flavour** is horizontal — the *same* version aimed at a different target. GitHub-flavour,
GitLab-flavour, Jira-flavour: identical code, different base URL, auth method, field mappings.
Not three copies. One script with three sets of amendments.

**And the two split cleanly by trust.** Flavour says *where and how* (public, shareable,
inert on its own). Secret says *as whom* (private, never travels). Which means you can publish
a whole working setup and it's safe — the recipe carries flavours, and each machine supplies
its own credentials locally.

The pretty consequence: flavour goes **into** the cache fingerprint and the secret stays
**out**. Two panes on GitHub-flavour with your work token and your personal token are the
same computation and correctly share cached work — with no credential anywhere near a
hash. That's tested with a planted canary token, grepped for across every memo key, the
serialized graph, the snapshot, the journal, and pane state.

---

## 6. What actually exists

```
src/kernel/      no DOM anywhere; runs in plain Node
  ids.js         content hashing, key-sorted stable stringify
  store.js       content-addressed node definitions, name index, search, presets
  arrangement.js patterns / panes / wires — and a deliberately separate LayoutStore
  scheduler.js   demand-driven, memoized, liveness-gated evaluation
  secrets.js     the fourth tier
  kernel.js      apply(command) in, events out — the entire public surface
src/runtime/     swappable execution: capability-built context, worker sandbox
src/nodes/       eleven starting nodes
src/ui/          a lens: landscape, canvas, views, omnibar, editor, menu
src/persist/     storage behind one small interface
test/            55 assertions, no dependencies, no browser
```

**"Content-addressed"** means every node is stored under a fingerprint of its own contents.
Change one character and it's a different node with a different address — which gives you
honest versioning for free, and means two people who wrote the same node have the same
node.

**The kernel's whole API is `apply(command)` in, events out**, both plain serializable data.
That's not tidiness; it's why putting a WebSocket in front of it later is mechanical rather
than a rewrite. The browser running standalone and the browser talking to a server on your
own machine become the same code with the pipe in a different place.

---

## 7. What's proven, and what isn't

The 55 tests are written as **claims that could be false**, not coverage. The ones worth
knowing:

- Change one source, and only its transitive dependents recompute. Everything else is untouched.
- Move a pane: the semantic graph is byte-identical. Rewire it: it changes.
- Two patterns sharing a node share the cached computation, not the state.
- An observed effect has still not run. Only an explicit trigger runs it.
- A pure node asking for the clock and the network receives neither.
- The kernel cooks a whole arrangement to completion in Node, with no browser, and exits clean.
- A planted secret appears in none of six places the system persists or hashes.

**Not proven:** the browser UI. I can run the kernel but not a browser, so the entire visual
layer is written and unexercised. That's the biggest open risk right now.

---

## 8. Where it's weak

Being honest about this matters more than the parts that work.

**Cycles are detected, not prevented.** You can wire A→B→A and nothing complains until
something asks for a value. It should be refused at the moment you draw it.

**The cache fingerprint hashes every input value on every request.** Fine for a
number, wasteful for a ten-thousand-row table. Values should carry a hash computed once
when they're produced. Related: a diamond-shaped dependency re-walks its shared upstream
several times per frame — the cache saves the computing but not the walking.

**Event wires are declared but not implemented.** A wire can say it carries events rather
than latest-value, but the bounded queue and the visible overflow state behind that promise
don't exist yet. Right now it's an honest label on a dishonest pipe.

**Storage will hit a wall** around a few hundred nodes with source attached, because v1
persistence is browser localStorage. It's behind an interface, so this is a swap rather than
a surgery.

**A pinned source in a background browser tab gets throttled** to roughly once a
minute. No amount of architecture fixes that; it wants a runtime living outside the browser.

---

## 9. What we learned about the idea itself

Three things surfaced only by building it.

**The invariant caught a real bug in my own code.** Not a style violation — the config
field was going to end up holding credentials, and credentials were going to end up in
cached fingerprints, because config is in the fingerprint by design. The rule found that
before it happened.

**Two "obvious" rules were wrong in practice.** Viewport-as-garbage-collector kills
sources you're still watching, until liveness propagates upstream. And "the pane owns status"
is fine, but one word for stale is not — "nobody's watching," "upstream failed," "the drive is
unplugged," and "paused" look identical and demand different reactions from you.

**The theme is a slot, not a scene.** I spent effort tuning one generated landscape. The
reference images showed four completely different worlds behind the same menu bar — so
the background wants to be pluggable, with the interface palette derived from whatever
world is loaded. Which is what the notes said all along: themes are nodes too. Worth noticing
how easy it was to start polishing the instance instead of building the slot.

---

## 10. If we keep going

Roughly in order of what de-risks most:

1. **Point a browser at it.** Everything above the UI is verified; the UI is not. Nothing else is worth doing until that's true.
2. **Cycle rejection and value-identity hashing.** Both cheap now, both expensive later.
3. **Split effects into a planner and an executor.** The planner is an ordinary pure node computing exactly what *would* be sent; the executor sends it when triggered. That's a dry-run preview for every effect in the system, and it's a convention rather than machinery.
4. **Rank search by what you're about to plug into.** If you drag a wire out of a port carrying a table and start typing, nodes accepting tables should surface first. This is the difference between a large library feeling like wealth and feeling like clutter.
5. **The runtime as its own process.** Then three screens, a phone, and a terminal are all just clients, and the pinned-source problem goes away.

The thing to keep testing at every step is the same question: *did this need the core to grow?*
So far the answer has been no about a dozen times running. The moment it becomes yes, that's
the signal something in the decomposition is wrong — and it's worth finding out early, while
the whole thing is still small enough to throw away.
