# NTDB: Exploring the Space

**Date:** 2026-08-07
**Mode:** Generative. Nothing here is a claim or a verdict. The goal is to map territory, find where the idea *wants* to grow, and collect more branches than we can use. Pruning is a separate session and a separate mood.

---

## Part 0 — The seed crystal

Take the word "database" off it. Take the biology off it too. What's the irreducible move?

> **A small, fixed-size, *mutable* marker living interstitially inside a large, *immutable* byte stream — where the marker learns from how the stream gets used.**

That's the whole invention. Everything else — towers, lighthouses, hippocampi, Moneyball — is one application of it.

Notice what this primitive is *not* committed to:

- It's not committed to being a database. It's a decoration protocol for any addressable byte stream.
- It's not committed to routing. The marker could summarize, compress, authorize, version, or execute.
- It's not committed to text queries. The stimulus could be anything with a vector form.
- It's not committed to *one* marker per block. Or to markers being disjoint. Or to a fixed size, once you allow generations.

Each of those uncommitted degrees of freedom is a direction to swim in. The prior documents committed all of them at once, on the first pass, to the first plausible values. That's normal for a first pass and it's why the space feels smaller than it is.

**The most important reframe available right now:** NTDB might not be a database competing with Postgres. It might be a *layer* that can be bolted onto things that already exist — Parquet files, git repos, S3 buckets, tar archives, log directories. Layers win where competitors die. "Introns for Parquet" is a weekend project that inherits an entire ecosystem instead of fighting it.

---

## Part 1 — The design-space cube

Here's an organizing device that turns "is this right?" into the much better question "which cell haven't we visited?"

Three axes. Every point is a different system.

### Axis A — What does the intron *contain*?

```
static stats  →  sketches  →  learned vector  →  holographic bundle  →  executable module
(min/max)       (Bloom)      (NTDB today)       (VSA, §2.2)           (the file is a program, §3.4)
```

### Axis B — What does it *route to*?

```
its own block  →  row-ranges  →  other introns  →  other files  →  other people's corpora
(NTDB today)      (finer)       (graph, §2.5)     (federation)    (semantic protocol, §3.6)
```

### Axis C — What *updates* it?

```
nothing  →  query feedback  →  offline consolidation  →  peer gossip  →  model gradient
(Parquet)   (NTDB today)      (sleep, §2.4)             (§2.6)         (§2.8)
```

NTDB as written sits at roughly **(learned vector, its own block, query feedback)** — one cell out of ~125. Most of the cube is empty and some of the empty cells are more interesting than the occupied one.

A few cells worth naming immediately because they're strange and cheap:

- **(sketches, other introns, nothing)** — a static intron *graph*. No learning at all. Might already beat the learned version, which would be a hilarious and useful finding.
- **(holographic bundle, its own block, offline consolidation)** — no online learning, all the intelligence in the nightly pass. Much more stable, much easier to reason about.
- **(learned vector, other people's corpora, peer gossip)** — a semantic protocol between organizations that never share data. This one is a company.
- **(executable module, other files, model gradient)** — the maximal cell. Probably 5 years out. Worth knowing it's there.

---

## Part 2 — Eight territories

Each: the jump, what it unlocks, and what it rhymes with elsewhere.

### 2.1 The interstitial layer — introns as a universal parasite

**The jump:** stop building a file format. Build a thing that *attaches* to file formats.

An intron needs three things from its host: a stable address, a byte range, and a place to stash 1KB. Enormous numbers of systems provide all three:

| Host | Where introns live | What emerges |
|---|---|---|
| Parquet | row-group key/value metadata (already a free-form dict!) | learned zone maps, no format change |
| Git repo | notes/refs, or a sidecar `.introns` | a repo that learns which functions are read together |
| S3 / object store | object user-metadata headers | a bucket that learns its own semantics |
| tar / zip archives | extended headers | searchable archives without extraction |
| Filesystem | xattrs on every inode | **an FS that learns which files you open together** |
| Video container | per-keyframe side data | learns which scenes get rewatched |
| A codebase | a comment above every function | the repo indexes itself as you read it |

That filesystem row is worth stopping on. Every OS has a file-open trace and throws it away. An xattr-resident intron layer would turn that trace into a learned locality map — Spotlight that improves rather than just indexing. That's a demo people *feel* immediately, and it needs no new storage format at all.

**Rhymes with:** the way DWARF debug info rides along inside ELF binaries. Nobody invented a new executable format for debugging; they found the interstitial space.

### 2.2 Holographic introns — the math upgrade that makes one object do three jobs

Right now the design has three separate metadata ideas fighting for the same 1KB: a learned routing vector, sketches/Bloom filters, and semantic tags. What if they're the same object?

There's an entire field built for this: [Hyperdimensional Computing / Vector Symbolic Architectures](https://arxiv.org/pdf/2111.06077) — Kanerva, Plate's Holographic Reduced Representations, Smolensky. It gives you three algebraic operations on high-dimensional vectors:

- **Bundling** (`+`) — superpose many things into one vector. The result is *similar to all of them*.
- **Binding** (`⊗`) — combine two vectors into a third that is *dissimilar to both*, and is invertible.
- **Permutation** (`ρ`) — encode order and sequence.

What this unlocks, concretely:

```
intron.vector  =  Σ over items in block:  ρ(position) ⊗ (column ⊗ value)
```

One 1KB vector that is *simultaneously*:

- a **router** (query resonates with it, as today)
- a **sketch** (bundling gives you approximate membership for free — it's a Bloom filter that also does similarity)
- a **structured record** (unbind by `column` and recover an approximation of the values)
- **composable** — bundle two introns and you get a valid intron for their union. Merging becomes addition.

That last property is huge and it cascades. If introns compose by addition, then:

- **Hierarchy is free.** A meso-intron is the sum of its micro-introns. The multi-scale fractal structure from the older chats stops being a design problem and becomes arithmetic.
- **Merging two databases is adding vectors.** No conflict resolution. This is a CRDT — see §3.2.
- **Deletion is subtraction.** Approximately, but that's the register we're in.

And critically for the M1 Air constraint: **VSA is model-free.** Random hypervectors, no training corpus, no download. It preserves exactly the property that makes this buildable by one person.

**Rhymes with:** holograms. Every fragment contains the whole at reduced resolution. Which suggests a resilience property no database has — lose 40% of your introns and you get a blurrier index, not a missing 40%. Worth testing purely because it would be *beautiful*.

### 2.3 The write side — learned placement and learned chunking

Every conversation so far is about retrieval. The write path is completely unexplored and it's half the system.

**Learned placement.** Today: append. But an intron could learn *where* to put incoming data — routing writes so that semantically similar data lands physically adjacent. Then query locality and storage locality converge over time. The file physically reorganizes toward the query distribution.

**Learned chunking.** Block boundaries are currently arbitrary. But "where should the cut go?" is a *segmentation* problem, and segmentation is learnable. Byte-pair encoding for data blocks. The system learns that these 4KB always get read together and these never do, and moves the intron boundary accordingly.

This one has a lovely recursive quality: **the introns decide where the introns go.** The structure that emerges determines the structure that emerges next.

**Sub-jump:** if boundaries are learnable, they're also *negotiable at read time*. Different readers could see different block boundaries over the same bytes — a "projection" in the truest sense. Two users, same file, different chunkings, each tuned to their query history.

### 2.4 Two-timescale memory — the metaphor becomes load-bearing

The "sleep cycle" idea is better than it's been given credit for, because there's real theory underneath it that predicts *why* it's necessary.

Complementary Learning Systems theory (McClelland, McNaughton & O'Reilly, 1995) says: you need a fast-learning system and a slow-learning system, plus replay between them, because a single system can't both absorb new information quickly and retain old information stably. That's not a metaphor — it's a mathematical necessity argument.

Applied here, an intron gets **two** weight vectors:

```
w_fast   high learning rate, high decay     — "what I've been asked lately"
w_slow   low learning rate, low decay       — "what I stably am"
score = α·(q · w_fast) + (1−α)·(q · w_slow)
```

And the sleep cycle is the consolidation transfer: replay the query log, and whatever `w_fast` learned that *survives* replay migrates into `w_slow`.

What this unlocks beyond stability:

- **The DB has a recent-interest state distinct from its identity.** You can ask "what has this corpus been about this month?" separately from "what is this corpus about?"
- **Snapshot `w_slow` over time and you get a trajectory.** The database remembers its own past beliefs. Query: *what did I think this folder was about in 2023?*
- **Two-timescale gives you a principled α dial** between recency and stability, which is the knob every search system fakes.

**Rhymes with:** fast weights in neural networks (Hinton & Plaut, and the modern fast-weight-programmer line), and with the L1/L2 cache hierarchy. It's the same shape at three different levels of the stack, which is usually a sign you've found something real.

### 2.5 The intron graph — routing grown bottom-up instead of clustered top-down

Lighthouses are currently defined top-down (hand-specified, or k-means over intron vectors). Alternative: grow them from below.

**The mechanism:** when intron A scores high on a query but fails verification, and intron B succeeds, A learns a weak edge → B. "I'm not it, but I know who is."

Run that for a while and you get a directed graph over introns, learned purely from misses. Then:

- Queries **walk** the graph instead of scoring everything. Sublinearity emerges instead of being designed.
- Densely-connected subgraphs *are* the towers. You never defined them; they precipitated.
- Hubs *are* the lighthouses. Same.
- Failure becomes information. Right now a miss is pure loss; here it's the only thing that builds the graph.

**The pretty part:** this is a small-world network self-assembling from query traffic. Six degrees of separation over your own data. And it's exactly what the older chat meant by "expander graph linking" — except it doesn't need to be constructed, it grows.

**Sub-jump:** if introns can point at other introns, they can point at introns *in other files*. The graph doesn't respect file boundaries. See §3.6.

### 2.6 Federation — trading semantics without trading data

The intron layer is tiny and contains no data — only *the shape of what's there*. That asymmetry is a gift and the notes haven't opened it.

Two organizations exchange intron weights. Neither sees the other's bytes. But:

- Each learns the other's semantics — how the other *carves* its domain.
- A query can be routed to whoever's introns resonate, then the actual retrieval is negotiated separately.
- Weight updates can be differentially private (they're already just vectors — noise addition is trivial).

**What this unlocks:** a search federation with no central index and no data sharing. Every participant keeps their data; everyone benefits from everyone's learned structure.

**The stranger version:** a *public* intron layer over a *private* corpus. You publish a 10MB file that describes the shape of your 10TB archive. Anyone can query it and be told *what exists* without seeing any of it. That's a new kind of object — see §3.1.

### 2.7 The fidelity dial — approximate and exact as a continuum

If an intron summarizes its block, then the intron *is* a lossy compression of the block. Which means some questions can be answered **without reading any data at all.**

```
read introns only        →  instant, fuzzy    ("roughly, yes, there's tennis stuff here")
+ read top-K blocks      →  fast, mostly right
+ read all candidates    →  slow, exact
+ full scan              →  ground truth
```

No database offers this as a *dial*. They offer one point on it and call the others someone else's problem. Exposing it as a parameter — `find(query, effort=0.1)` — is a genuinely novel interface, and it maps perfectly onto agent use, where an agent should spend cheap when exploring and expensive when committing.

**Sub-jump:** approximate query processing for free. `count`, `exists`, `roughly how much` answerable from the intron layer alone, at RAM speed, over petabytes.

### 2.8 Introns as persistent attention

Here's a reframe of "the LLM thinks inside the database" that makes it mechanically concrete rather than aspirational.

An LLM's KV cache is a set of (key, value) pairs that attention scores against. It's ephemeral, huge, and thrown away every session.

**What if the intron layer just *is* a persistent, shared, on-disk KV cache?**

- Intron weight vector = the **key**
- Data block = the **value**
- Query vector = the **query**
- Scoring = **attention**, literally, not by analogy

This isn't a wild jump — [Memorizing Transformers](https://arxiv.org/abs/2203.08913) (Wu et al., ICLR 2022) already does kNN attention over an external non-differentiable memory of past (key, value) pairs, scaling to 262k tokens, with a learned gate blending local attention and memory attention. The pieces exist and are proven.

What NTDB adds to that picture: their memory is a flat kNN store. Yours is *structured, learned, self-organizing, and persists across sessions and across users.* The learned gate becomes a lighthouse. The memory becomes a file you can copy.

**And the recursion:** if the intron layer is attention, and attention is what transformers are made of, then stacking lighthouse layers over intron layers is *literally building a transformer whose parameters live on disk and whose activations are your data.* The "cortical layer" intuition from the chat isn't a metaphor at that point — it's a description.

---

## Part 3 — Cloud jumps

The wilder end. Lower probability, higher ceiling. Included because pruning happens later.

### 3.1 The shadow index — ship the map, not the territory

Extract just the intron layer. It's ~10MB for a 10TB corpus. It contains no data.

That artifact is a **new kind of object**: a searchable, queryable, *learnable* description of a corpus you do not have.

- Publish the shadow index of your archive. People search it, discover what exists, then request only the blocks they want.
- Scientific data: a lab publishes the shadow of a dataset too large or too sensitive to release. Others query the shape, then negotiate access to slices.
- The shadow index is git-friendly, emailable, and improves as people query it.
- **Shadow indexes compose** (if you use VSA per §2.2). Sum ten shadows, get a federated index over ten archives you don't have.

This might be the most commercially strange and interesting object in the whole space.

### 3.2 Databases that merge like git branches

Data blocks are immutable and append-only. Introns are small and (under VSA) compose by addition. Therefore merging two NTDBs is: concatenate blocks, add intron vectors. **Conflict-free by construction.** It's a CRDT.

What that unlocks: fork your database. Work offline. Merge back. Two people index the same corpus differently and their learned structures *combine* rather than conflict. A team's shared understanding of a corpus accretes the way a git history does.

No database can do this. Not one. The append-only + composable-metadata combination is what makes it possible, and both properties are already in the design for other reasons.

### 3.3 The negative-space index — knowing what you don't have

Every index answers "where is X?" None answer "what's missing?"

But an intron layer trained on a query stream *does* know which queries consistently fail. Accumulate the failures and you get a map of the holes:

- "You have no data about Q3 2024 shipping costs, and you've looked eleven times."
- A research corpus that tells you where the literature is thin.
- An agent that knows the boundary of its own knowledge — which is the single most useful thing an agent could know and the thing they're currently worst at.

**The inversion:** the database queries *you*. It notices a persistent gap and asks you to fill it. Storage that has an appetite.

### 3.4 The file as a program

If introns can hold executable modules (Axis A's far end), then reading the file *runs* it. Storage/compute distinction dissolves.

Weaker, buildable version: introns carry tiny learned decoders. The block is compressed with a codec the intron *learned* for that specific block's statistics. Self-describing, self-decompressing data — each block bringing its own optimal codec in 200 bytes.

Stronger version: the format describes its own parser in introns. The file is recoverable from any sufficiently large fragment because the parser is redundantly distributed through it. Genuinely biological — this is what makes DNA robust.

### 3.5 Self-play indexing

Buried in the older chat is a beautiful idea that got treated as a test harness: *"a program that continuously creates truly random goals for the search function to find."*

That's not a benchmark. That's **self-play.**

- A generator proposes queries designed to *defeat* the current index.
- The index adapts to cover them.
- The generator escalates.

It's GAN structure, or AlphaZero structure, applied to index optimization. The database trains against an adversary that's actively hunting its blind spots — and it can do this *while idle*, forever, with no user traffic. A database that practices.

Combine with §3.3: the adversary is specifically searching for negative space. The system maps its own holes by attacking itself.

### 3.6 Introns as an inter-corpus protocol

If introns can reference introns in other files (§2.5 sub-jump), the graph stops respecting file boundaries. Then:

- Your notes' introns point at a paper's introns point at a dataset's introns.
- A citation graph that isn't declared but *learned from co-access*.
- The unit of the web becomes the block, not the page, and the link is learned rather than authored.

This is Vannevar Bush's actual Memex proposal — his central mechanism was the *associative trail*, built by users traversing documents, not by authors writing links. The chats invoked Memex for the storage part. The trails are the part nobody built, and the intron graph is a mechanism for them.

### 3.7 Complex-valued introns — logic through phase

Weights are real today. Make them complex and phase becomes available:

- **Negation** = phase inversion. "alice AND NOT bob" cancels rather than requiring a separate mechanism.
- **Ordering/sequence** = phase rotation (this is exactly what VSA's permutation does, and what RoPE does in transformers).
- Constructive/destructive interference across introns means a query can be answered by a *pattern* over many introns rather than a winner — genuinely holographic retrieval.

Same memory budget: 128 complex numbers = 256 floats. Free upgrade, if the algebra works out.

### 3.8 Serendipity as a designed output

Every exact database treats a near-miss as failure. Here, near-misses are *free* and *semantically meaningful* — they're things that resonated but didn't verify.

Make that a feature:

- **Dream mode** — deliberately query at low confidence to surface unexpected juxtapositions between distant towers. A creativity tool over your own archive.
- The "sleep cycle" LLM walk isn't just maintenance; its outputs are *interesting to a human*. "While consolidating, I noticed your Physics and Philosophy towers both cluster around 'time' — want a lighthouse?"
- The database as collaborator rather than retrieval service.

For a personal knowledge base, this might be the actual killer feature. Nobody needs faster grep. Plenty of people would pay for a thing that notices connections in their own archive that they didn't.

---

## Part 4 — Where the space already has allies

Not competition. Fuel. Each of these built a piece and left it lying around:

| Field | What they built | What you take |
|---|---|---|
| [HDC / VSA](https://arxiv.org/pdf/2111.06077) (Kanerva, Plate) | binding, bundling, unbinding on random hypervectors | §2.2 — the whole algebra, model-free |
| [Memorizing Transformers](https://arxiv.org/abs/2203.08913) (Wu et al. 2022) | kNN attention over external persistent memory + learned gate | §2.8 — proof the attention-over-disk path works |
| [Database Cracking](https://www.cidrdb.org/cidr2007/papers/cidr07p07.pdf) (Idreos et al. 2007) | physical reorganization as a byproduct of querying | §2.3 — the write-side precedent, and it just won [CIDR's Test-of-Time award](https://www.cwi.nl/en/news/tidying-up-while-you-search-cwi-paper-database-cracking-wins-cidr-test-of-time-award/) |
| Complementary Learning Systems (McClelland et al. 1995) | *why* you need fast+slow+replay | §2.4 — turns the sleep metaphor into an argument |
| [Differentiable Search Index](https://arxiv.org/abs/2202.06991) (Tay et al. 2022) | retrieval entirely inside model parameters | the contrast case — you put the parameters on disk instead |
| CRDT literature | conflict-free merge from algebraic structure | §3.2 — the merge story is already formalized |
| Feature hashing (Weinberger 2009) | model-free vectors with real locality | the cheap encoder that keeps the M1 Air property |

The recurring shape: **half of this has been built, in pieces, in fields that don't talk to each other.** That's usually where the interesting work is — not in unclaimed territory, but at the seams between claimed ones.

---

## Part 5 — Grouping: four things that might each be their own project

If I squint at everything above, the branches clump into four projects that don't have to be the same project:

**① The Parasite.** Introns as a bolt-on layer for existing formats (§2.1). Smallest, most immediately useful, inherits ecosystems instead of fighting them. The learning-filesystem demo lives here.

**② The Algebra.** VSA introns that compose, merge, and hierarchize by arithmetic (§2.2, §3.2, §3.7). Most mathematically elegant, highest chance of a real theoretical result, and it makes several hard problems disappear rather than solving them.

**③ The Organism.** Two-timescale weights, intron graphs, sleep cycles, self-play, negative space (§2.4, §2.5, §3.3, §3.5). Most faithful to the original vision. The one where "biomimetic" earns its keep.

**④ The Cortex.** Introns as persistent attention, differentiable retrieval, agent memory (§2.8). Biggest ceiling, most crowded field, most dependent on things outside your control.

They share a substrate but they're different bets with different timelines. Worth knowing which one you're actually excited about, because that changes what gets built first — and they don't compete for the same evidence.

---

## Part 6 — Parked

The earlier critique isn't deleted, it's just not this session's job. Held for whenever we switch modes: the encoder-locality question, the norm-stability question, the row-vs-column granularity question, and the block-size/verification coupling. All four are cheap to settle empirically and none of them threaten the *space* — at worst they relocate which cell of the cube is the good one.

One note that belongs here rather than there: several of the wilder branches above (§3.2 merging, §2.2 composition, §3.1 shadow indexes) actually *depend on* getting the algebra right rather than the learning rule. Which is a nice result — it means the most speculative directions may be the ones with the firmest foundations, and that the algebra question is more load-bearing than the learning question. That's the opposite of what the first pass assumed.
