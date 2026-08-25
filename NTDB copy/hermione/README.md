# Hermione

A bag you drop everything into. Small opening, everything inside, nothing lost.

*Generation 1 + iterations 1a-1d. See [GENERATIONS.md](GENERATIONS.md) for what
each one added and closed, and [EXPLAINED.md](EXPLAINED.md) for how it works
from the bytes up.*

No folders. No tags. No schema. No "where should this go". Drop files in and
search them. It works on day one, and it gets better while you're asleep.

```bash
hermione init
hermione add ~/Documents ~/Downloads ~/Desktop
hermione find "that boat thing from spring"
hermione ask  "Hermione, remind me what I added this week"
```

---

## The shape of it

An LSM tree where compaction is comprehension. Writes land raw, flush into a
deterministic index, and consolidate into a learned one. Classic databases
compact when they're under pressure; this one compacts when it's bored.

```
        drop
          |
    +-----v------+   append-only, content-addressed, never lies
    |   LEDGER   |   <-- the only precious thing
    +-----+------+
          |  digest: extract -> chunk
    +-----v------+   SQLite FTS5. Exact, boring, works today.
    |   CLERK    |   Also: grades the Cortex's homework, forever, for free.
    +-----+------+
          |  consolidate (idle) / dream (nightly)
    +-----v------+   learned introns: 1 per chunk, fast + slow weights,
    |   CORTEX   |   Bloom filter, cosine routing
    +------------+
```

### The contract

    The Ledger is truth.
    The Clerk is derived from the Ledger.
    The Cortex is derived from the Ledger and the query log.
    Delete either index at any time. Nothing is lost but time.

This is not a slogan, it's a tested invariant (`test_indexes_are_disposable`).
It means you can experiment destructively on the learned layer -- swap the
encoder, change the learning rule, run competing Cortexes side by side -- with
zero risk to anything that matters.

---

## The Race

Every query runs through both layers in parallel, and the *disagreement* is
the training signal:

| outcome | meaning | what happens |
|---|---|---|
| Clerk hit, Cortex missed | failed a question with a known answer | free labelled example |
| both hit | agreement | rank up |
| Clerk missed, Cortex hit | **the reason this exists** | reinforce hard |
| both missed | a hole | negative-space map |

**The Clerk grades the Cortex's homework, forever, automatically.** No thumbs
up/down, no hand-labelled query set, no synthetic benchmark to accidentally
rig. Every real query is a data point, and `hermione eval` reports the one
honest number: results the learned layer found that exact search could not.

The Cortex's job is not to replace exact search. It is to answer the queries
exact search fails on -- cross-format, cross-language, vague, half-remembered.

### What that looks like

`sparrow` appears in no file in this corpus, so the Clerk returns nothing,
forever. Use it a few times alongside words the Clerk *can* match, and:

```
$ hermione find sparrow -v
~ sailing-notes.md
~ meeting.html
~ fluid-dynamics.md
~ invoice_yard_2024.csv
  clerk 0 hits (0.2ms) | cortex 22 (0.4ms)
```

It learned your private name for a project out of a corpus that never contains
it. That's the whole product in one command.

---

## Install

Python 3.9+. Only hard dependency is numpy.

```bash
pip install numpy
pip install pypdf beautifulsoup4 python-docx   # optional: pdf, html, docx
export PYTHONPATH=/path/to/hermione
alias hermione='python3 -m hermione.cli'
```

Bags default to `~/.hermione`, override with `--bag` or `$HERMIONE_BAG`.

> Keep the bag on **local disk**. SQLite needs POSIX locking that iCloud
> Drive, Dropbox and network shares don't provide. The bag is only an index --
> point it at your synced folders with `hermione add` and the originals stay
> wherever they are. (Hermione detects this case and tells you.)

## Commands

| | |
|---|---|
| `init` | create a bag |
| `add PATH...` | drop files or folders in |
| `note TEXT` | drop a note in |
| `find QUERY` | search; races both layers (`-v` for the scoreboard) |
| `status` | what's in the bag |
| `consolidate` | fast weights -> slow weights |
| `dream` | cluster into towers, surface structure |
| `forget` | decay unused weights |
| `rebuild` | rebuild both indexes from the Ledger |
| `relearn` | replay the query log as training |
| `eval` | the built-in benchmark |
| `ask` | talk to it — additions, interests, gaps, whereabouts, versions `[1d]` |
| `vault` | attach or list vaults `[1b]` |
| `unreachable` | what you know about but can't open right now `[1b]` |
| `history` | what happened, over a window `[1c]` |

Result markers: `=` both layers agree · `|` exact match · `~` association.

## The metabolism

Six processes on different clocks. Wire the last three to `cron`/`launchd`:

```
ingest       on drop          bytes -> Ledger, instant, never blocks
digest       after ingest     extract, chunk -> Clerk, Cortex
circulate    on query         race the layers, log the outcome
consolidate  idle, minutes    fast weights -> slow weights
dream        nightly          cluster into towers, surface connections
forget       weekly           decay, compact, tier out
```

Idle time is the resource. Your machine is idle ~90% of the time and every
other piece of software wastes it.

---

## Design notes

Things that are load-bearing and easy to get wrong:

**The encoder hashes features, not the query string.** A hash of the whole
string has zero locality -- SHA-256 is *designed* so one changed character
rewrites half the output, so "alice email" and "alice address" would land
maximally far apart and the index could only respond to queries it had
literally seen before. Signed feature hashing (words + character trigrams)
gives real cosine similarity, plus typo tolerance, at ~20us and no model.
Guarded by `test_encoder_has_locality` and
`test_learning_is_not_pure_memorisation`.

**Scoring is cosine, and weights are renormalised after every update.** With a
raw dot product, score scales with `||w||`, so a frequently-queried intron
starts winning on loudness rather than fit, and under any skewed workload a
handful of introns capture the top-K for everything. Guarded by
`test_weights_stay_unit_norm_under_hammering`.

**Candidate selection is epsilon-greedy.** Without exploration an intron
outside the initial top-K is never read, therefore never updated, therefore
never enters the top-K -- permanently invisible. It's an explore/exploit
problem, not a supervised one. Guarded by `test_exploration_reaches_everything`.

**The Bloom filter is a bonus, not a gate.** Gating is tempting since Bloom has
no false negatives, so rejections are provably safe on literal grounds. But the
Cortex exists to answer queries whose words never appear in the target text,
and a gate prunes exactly those. Lexical evidence outranks learned association;
it never silences it.

**Bloom filters are stored byte-major, `[nbytes, N]`.** Probing touches one
byte offset across all introns at once; stored row-major that's a strided
gather and it dominates query cost. Measured at 500k introns: 74ms row-major
vs 4.3ms transposed. Guarded by `test_bloom_storage_is_byte_major`.

**Two timescales.** `w_fast` is what you've been asking about lately; `w_slow`
is what the corpus stably is. Consolidation migrates one into the other. A
single-timescale system can't both absorb quickly and retain stably --
Complementary Learning Systems, in miniature.

**Towers are not declared, they precipitate.** `dream` runs k-means over the
slow weights. On the demo corpus it discovers a tower spanning
`contacts.json + invoice.csv + meeting.html + sailing-notes.md` -- four
formats, no schema, nobody told it they were related.

### Measured

500k introns, dim=128, on one core:

| introns | score | bloom | full query | index |
|---|---|---|---|---|
| 10k | 0.4ms | 1.6ms | 1.7ms | 13 MB |
| 100k | 5.3ms | 1.8ms | 8.4ms | 128 MB |
| 500k | 15.7ms | 5.8ms | 28.0ms | 640 MB |

~1.3 KB of index per chunk, independent of how much text the chunk holds.

---

## Built to be extended

Every layer that's meant to be replaced sits behind a registry:

```python
from hermione.extract import extractor
@extractor(".epub")
def _epub(data, entry): ...

from hermione.cortex.encoder import register
@register("embedding")          # drop-in; index doesn't change
class EmbeddingEncoder: ...

from hermione.chunk import chunker
@chunker("learned")             # "where to cut" is a segmentation problem
def learned_chunker(text, size, overlap): ...
```

Threads that are designed for but not built:

- **Intron graph** — introns pointing at introns, grown from misses. Towers
  and lighthouses precipitate instead of being clustered.
- **VSA / hyperdimensional introns** — bundling and binding, so introns
  compose by addition. Merging two bags becomes vector addition; a CRDT.
- **Shadow index** — ship `cortex/` without the Ledger: a searchable
  description of a corpus you don't have. Already ~1.3KB/chunk.
- **Learned chunking** — the introns decide where the introns go.
- **Surfaces** — MCP server (agent memory), FUSE mount (`~/mind/boat/` as a
  folder that doesn't exist until you look), hotkey overlay.
- **Self-play** — a generator proposing queries designed to defeat the index,
  running on idle time. A bag that practices.

## Tests

```bash
python3 -m pytest tests/ -q     # 45 passed
```

`tests/test_core.py` is Generation 1's contract and has not been edited since
it was written -- that is the point of the generation split. `tests/test_iter1.py`
covers iterations 1a-1d.

They're written as executable specifications of the invariants above, not
coverage. The decisive one is
`test_cortex_learns_associations_exact_search_cannot`: if that fails, the
premise is wrong and the rest doesn't matter.
