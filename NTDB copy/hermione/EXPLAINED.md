# Hermione, from the metal up

Each layer assumes only the ones before it. No layer is a metaphor for the next one — every step is a real thing that happens to real bytes. Read as far as you want and stop; each level is a complete picture at its own scale.

*Current state: Generation 1 + iterations 1a–1d.*

---

## Layer 0 — A file is a run of bytes, and that's all it is

Your disk holds numbered slots. A file is a run of them plus a name in a table.

Everything after this is about one problem: **that table only knows names.** The filesystem can tell you `boat.md` exists. It cannot tell you the boat file, the yard invoice, and the meeting notes are about the same thing, because it has never looked inside any of them.

Two ways out. Look inside everything, every time (grep — always correct, always slow, learns nothing). Or look inside once and write down what you found (an index — fast, but only answers questions you anticipated).

Hermione does both and lets them argue. That's the whole design.

---

## Layer 1 — The Ledger: a list you only ever append to

Drop in `boat.md`. First thing that happens:

```python
digest = sha256(data).hexdigest()      # a fingerprint of the bytes
entry_id = digest[:16]
```

SHA-256 turns any run of bytes into 64 hex characters. Same bytes → same fingerprint, always. Different bytes → different fingerprint, reliably enough to bet a filesystem on.

That gives you three things for free:

- **Identity without names.** Two copies of the same file in different folders are one thing.
- **Integrity.** Re-hash later; if it differs, the bytes rotted.
- **Deduplication.** Already have that fingerprint? Nothing to do.

Then one line is appended to `journal.jsonl`:

```json
{"entry_id":"a3f2...","name":"boat.md","size":812,"ingested_at":1754... ,
 "parent_id":"","presence":"here","vault_id":""}
```

**Append-only, never edited.** Delete is a *tombstone* — another line saying "consider that gone." The bytes stay.

That sounds like hoarding. It's the thing that makes everything downstream safe: **if the record of what happened can never change, every other structure in the system is disposable.** Get the Cortex badly wrong, delete it, rebuild. Nothing is at stake. That freedom is bought entirely here.

> **`presence` and `vault_id` — iteration 1b.** The journal is light (~200 bytes/entry); the bytes are heavy. They should be able to travel separately. `presence` records whether the bytes are in the bag, in a named vault, merely linked, or gone. So the bag can say *"that file is on the black SSD, here's what's in it, go plug it in."* Search a drive that isn't connected.

> **`parent_id` — iteration 1a.** More on this at Layer 8.

---

## Layer 2 — Digest: bytes become text, text becomes chunks

A PDF is compressed binary. A `.docx` is a zip full of XML. Each format needs its own reader, so `extract.py` is a registry — one function per extension, `_fallback` for anything else (decode, check it looks like text, keep it or don't).

Then the text is cut into **chunks** of ~1200 characters on paragraph boundaries.

Why cut at all? Two reasons that pull the same direction:

- **Retrieval granularity.** "Which of these 40 pages" is a more useful answer than "this document."
- **Bloom filters die on big blocks.** See Layer 5 — this is a hard numeric constraint, not a preference.

Each chunk gets an id: `{entry_id}:{ord}`. **One chunk is the atom of this system.** Everything above operates on chunks.

---

## Layer 3 — The Clerk: the boring half, and it is not optional

Chunks go into SQLite with FTS5. It builds an inverted index: for every word, the list of chunks containing it. Search "mainsail" → look up the word → get the list. No scanning.

This is 1970s technology and it is *excellent*. Exact, fast, predictable, no training, works on the first query.

**The Clerk is here for two reasons, and the second is the surprising one.**

1. The bag is useful on day one, before anything has learned anything.
2. **The Clerk is the teacher.** Hold that thought until Layer 7.

---

## Layer 4 — The encoder: turning text into a direction

Here's the first genuinely unusual step.

We want to compare meanings arithmetically. So: turn text into a list of 128 numbers — a *vector*, which you can think of as an arrow in 128-dimensional space. Then "similar" becomes "pointing the same way," and that's just multiplication.

The obvious approach is a language model. But that's gigabytes, and slow, and a dependency.

So instead:

```python
for feature in ["boat", "keel", "^ke", "kee", "eel", "el$", ...]:
    h = blake2b(feature)
    bucket = h[:4] % 128          # which of the 128 slots
    sign   = +1 or -1 from h[4]   # which direction
    v[bucket] += sign
v /= length(v)                    # scale to length 1
```

Every word — and every 3-letter fragment of longer words — is hashed to a slot and a direction. Add them all up. Normalise.

**Why this works.** "boat keel" and "boat halyard" both push the `boat` slot the same way, so their arrows partly align. Unrelated text pushes different slots, so its arrow doesn't. Meaning becomes geometry — with no model, in about 20 microseconds.

**Why the signs matter.** With only 128 slots, unrelated features collide constantly. Random ± directions make collisions cancel out on average instead of piling up into a bias.

**Why 3-letter fragments.** "hermione" and "hermoine" share almost all their trigrams. Typo tolerance for free.

> **The mistake this avoids.** The obvious move is hashing the whole query string. That fails completely, and quietly. SHA-256 is *designed* so one changed character rewrites half the output — "alice email" and "alice address" would be as far apart as "alice email" and "tectonic uplift." An index built on it could only ever answer queries it had *literally seen before*: a lossy hash table wearing a learning rule. It would look fine in any benchmark that tests on its training queries. `test_encoder_has_locality` exists to make sure this never creeps back.

---

## Layer 5 — The intron: a small tag that learns

Every chunk gets an **intron** — about 1.3 KB of metadata sitting beside it:

```
w_slow    128 floats   what this chunk stably is
w_fast    128 floats   what it's been asked about lately
bloom     256 bytes    which words are definitely NOT in here
stats     hits, misses, born
```

**`w_slow`** starts as the chunk's own encoded text — what it knows before anyone asks anything.

**`bloom`** is the exact half. Hash each word to 6 bit positions in 2048 bits, set them. To test a word, check those 6 bits: any bit clear → **definitely absent**. All set → probably present. Never a false negative, sometimes a false positive.

That last property is the useful one: it can *rule out* chunks with certainty, reading 6 bits and touching no data.

> **Why chunks are ~1200 characters.** Bloom accuracy collapses as you add words to a fixed number of bits: at 2048 bits, ~300 distinct words gives a 1.7% false-positive rate, 500 gives 8.6%, 1200 gives 37% — useless. Chunk size isn't a tuning knob, it's set by this table.

**The whole index is ~1.3 KB per chunk regardless of chunk content**, which is what makes the rest of it cheap: 500,000 chunks is 640 MB, and it lives in RAM.

---

## Layer 6 — Searching is one multiplication

Encode the query. Multiply the whole stack of intron vectors by it:

```python
scores = alpha * (w_fast @ q) + (1 - alpha) * (w_slow @ q)
```

`w_slow @ q` is one matrix multiply: every chunk scored against the query at once. Not a loop, not a tree, not pointer-chasing — one dense arithmetic operation, the thing modern CPUs are fastest at. **15ms for half a million chunks.**

The Bloom filters add a bonus to chunks that might literally contain a query word.

> **A bonus, not a gate — and this was a real bug during Generation 1.** Gating is tempting: Bloom has no false negatives, so rejecting is provably safe *on literal grounds*. But the Cortex exists precisely to answer queries whose words appear nowhere in the target text. A gate prunes exactly those. Literal evidence should outrank learned association; it must never silence it.

Then ε-greedy: ~8% of the time, throw a random chunk into the candidates.

> **Why randomness is mandatory.** Only chunks that get looked at get updated. A chunk whose initial vector never lands in the top-K is never examined, never updated, and can never *become* top-K. Permanently invisible — not rare, the default for most of a corpus. This is an explore/exploit problem, not a search problem.

---

## Layer 7 — Learning, and where the labels come from

Every query runs through **both** layers, in parallel. Then:

| | |
|---|---|
| Clerk found it, Cortex missed | pull that intron toward the query |
| Both found it | agreement, rank up |
| Cortex found it, Clerk couldn't, text confirms | the point of the whole thing — reinforce |
| Neither | a hole; remember it |

```python
w_fast[i] += 0.08 * q
w_fast[i] /= length(w_fast[i])      # back to length 1
```

**Where the training data comes from is the good part.** No thumbs up/down, no labelled set, no benchmark to accidentally rig. **The Clerk's answers are the labels.** Exact search is right by construction, so every query it can answer is a free supervised example — automatically, forever, from ordinary use.

Which defines the Cortex's job precisely: *not* to replace exact search, but to answer the queries exact search fails on.

**Why renormalise.** Score scales with vector length. Without renormalising, a frequently-queried intron grows long and starts winning on *loudness* rather than fit — under any realistic (skewed) workload, a handful of introns capture the top-K for everything and the index gets worse than random.

**Why two speeds.** `w_fast` learns quickly and fades; `w_slow` barely moves. `consolidate` migrates fast into slow. One system can't both absorb quickly and retain stably — you need two and a transfer between them. (This is also why brains do it, and it's the same shape as an L1/L2 cache.)

### What this produces

Search `sparrow` — a word in none of your files. Exact search returns nothing, forever. Use it a few times next to words the Clerk *can* match, and the Clerk teaches the introns that direction. Afterwards `sparrow` alone returns your four boat files out of a corpus that never contains the word.

That's the thing exact search structurally cannot do.

---

## Layer 8 — Lineage *(iteration 1a)*

Edit `boat.md`. Different bytes → different fingerprint → a new entry.

Which means, in Generation 1: new chunks, new introns, seeded from content, **carrying none of the learning.** You taught it `sparrow`, you edited the file, it forgot. No error. Nothing looked wrong.

The fix is one field, `parent_id`, and one consequence:

```python
w_slow[new] = w_slow[predecessor]      # inherit, don't restart
w_fast[new] = w_fast[predecessor]
```

Matching new chunks to old ones is done by comparing **encoded content** — deliberately not `w_slow`, which has drifted with training. "Is this the same paragraph, edited?" is a question about text. And comparing by similarity rather than position survives an inserted paragraph, which shifts every position but no similarity.

**An intron becomes a lineage rather than a snapshot.** Learning survives editing, which for a living corpus is the difference between accumulating and leaking.

---

## Layer 9 — The metabolism

Six processes on different clocks:

```
ingest       on drop         bytes -> Ledger, instant, never blocks
digest       after ingest    extract, chunk -> Clerk, Cortex
circulate    on query        race the layers, log the outcome
consolidate  idle            fast weights -> slow weights
dream        nightly         cluster into towers, surface connections
forget       weekly          decay unused weights
```

**`dream`** runs k-means over `w_slow`: group chunks whose vectors point similar ways. On the demo corpus it discovers a cluster spanning `contacts.json + invoice.csv + meeting.html + sailing-notes.md` — four formats, no schema, nobody told it they were related. Nobody declares a category; categories precipitate.

**Idle time is the resource.** Your machine is idle most of the time and every other program wastes it. This one eats it, which produces the property worth designing for: *you come back and it's better than when you left.*

---

## Layer 10 — The voice *(iteration 1d)*

```
$ hermione ask "Hermione, remind me what I added this week"
You added 3 things this week, one a revision of something you already had.
  boat.md
  sourdough.md
```

Under it: a keyword router over the corpus journal, the Ledger, and the introns. Pattern-match the phrasing to an intent, compute the answer, say it in a sentence.

**There is deliberately no model here.**

The bag's entire claim on your trust is that it doesn't make things up. A generative layer on top of an honest index inherits none of that honesty and spends all of it. So when a question doesn't match a known shape, Hermione says so plainly and searches instead. Falling back is always defensible; guessing never is.

A model belongs here eventually as a *better parser* — phrasing to intent — and never as the answerer. **The answers stay computed.**

> One bug from this iteration is worth keeping in mind, because it's the shape of mistake this design invites. `what am I missing` first reported every query with no literal match as a gap — including every codeword you had deliberately taught it. The second attempt compared the top score against the spread of all scores, and was wrong for a subtler reason: in any 60-sample distribution, something is always two standard deviations out, so it measured coincidence rather than learning. The right signal is `w_fast` alone — it is *exactly zero* until an intron is reinforced, so any real value there means someone taught this. Nearby-looking statistics were measuring the wrong thing entirely.

---

## The whole thing, in one paragraph

Bytes get fingerprinted and written to a list that only grows. Text is pulled out and cut into chunks. Each chunk goes into an ordinary exact index, and gets a 1.3 KB tag holding a direction in 128-dimensional space and a bitmap of its words. Searching multiplies one query direction against every tag at once. Both indexes answer every query, the exact one grades the learned one for free, and the learned one slowly becomes able to answer things the exact one never could. When you edit something, its tag inherits what it already learned. At night it clusters itself and notices what belongs together. And you can ask it, in a sentence, what you've been doing.

Nothing above is a metaphor. It's all just multiplication, hashing, and a list you don't erase.
