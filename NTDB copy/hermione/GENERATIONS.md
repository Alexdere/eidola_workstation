# Generations

Every addition and removal, recorded. The convention:

- **Generation N** — a coherent whole that stands on its own and passes its own tests.
- **Iteration Na, Nb, …** — a single graft onto that generation. One idea, testable in isolation, marked `[Na]` inline in the code.
- A new **Generation** is declared only when the shape changes enough that the previous generation's tests no longer describe the system.

Rules that make this worth doing:

1. **Previous-generation tests keep passing, unchanged.** `tests/test_core.py` is Generation 1's contract. It has not been edited since. If an iteration needs to change it, that iteration is really a new generation and should say so.
2. **Every iteration says what it closes.** Not what it adds — what was wrong or missing without it.
3. **Removals get recorded too**, with the reason. A design that only accretes never learns anything.

---

## Generation 1 — the core
*Status: complete, 24 tests, `tests/test_core.py`*

Three organs and a metabolism.

| | |
|---|---|
| **Ledger** | append-only, content-addressed journal + blob store. The only precious thing. |
| **Clerk** | SQLite FTS5. Exact, boring, works on day one. Also grades the Cortex. |
| **Cortex** | one learned intron per chunk: fast/slow weights, Bloom filter, cosine routing. |
| **Race** | both layers answer every query; the disagreement is the training signal. |
| **Runtime** | ingest · digest · circulate · consolidate · dream · forget. |

**Decisions made during Gen 1, kept:**

- Signed feature hashing, not whole-string hashing. A hash of the whole query has zero locality, so the index could only ever answer queries it had literally seen.
- Cosine scoring with renormalisation after every update, to prevent frequently-queried introns winning on magnitude rather than fit.
- ε-greedy candidates, or introns outside the initial top-K are permanently invisible.
- Bloom as a **bonus, not a gate** — a gate would prune exactly the cross-vocabulary queries the Cortex exists to answer.
- Bloom stored byte-major `[nbytes, N]`; row-major probing measured 17× slower at 500k introns.

---

## Iteration 1a — lineage
*Files: `ledger.py`, `cortex/index.py`, `clerk.py`, `runtime.py`*

**Closes:** the Cortex was amnesiac about edits, invisibly.

Content addressing means an edited file gets a new `entry_id` with no relationship to its predecessor. Its introns were re-created from scratch, seeded from content, carrying none of the learning. You could teach the bag that "sparrow" means a document, edit the document, and the association was silently gone. No error, no warning — the system just stopped accumulating the one thing it exists to accumulate.

**Added**

- `Entry.parent_id` — the entry this one revises.
- `Ledger.lineage()`, `Ledger.heads()`, `Ledger.children()`.
- `Cortex.add(..., inherit=)` — new introns start from a predecessor's weights instead of their content signature.
- `Cortex.match_chunks()` — maps new chunks to the ones they replace, by **content** cosine.
- `Bag._head_index()`, `Bag._inheritance()` — detection on ingest.
- `Clerk.entry_chunks()`.
- `rebuild()` replays in ingest order so lineage reconstructs.

**Decisions**

- *Matching on content, not on `w_slow`.* `w_slow` drifts as an intron learns, so a well-trained chunk would match its own revision poorly. "Is this the same paragraph, edited" is a question about text.
- *Matching by similarity, not by chunk ordinal.* Insert a paragraph at the top and every ordinal shifts; every similarity holds. Pinned by `test_1a_match_chunks_survives_insertion`.
- *Filename as the first-pass heuristic.* False positives cost nothing — chunks that don't match simply don't inherit.
- *`_head_index` built once per ingest.* Per-file was quadratic, and a dump is exactly where that bites.

**Known rough edge:** search now returns both the old and new version of a revised file. Correct, but noisy. Lineage-aware ranking (prefer heads, collapse superseded) is a candidate for 1e.

---

## Iteration 1b — presence and vaults
*Files: `ledger.py`, `runtime.py`, `cli.py`*

**Closes:** the Ledger could not say *"I know this exists, I don't have the bytes."*

`add_file` copied every blob into the bag. Correct for text, fatal for a real dump — the first 40GB of video ends it. It also made the bag heavy, quietly killing the properties that made it interesting: portable, emailable, git-committable.

**Added**

- `Entry.presence` ∈ `{here, vaulted, linked, missing}` and `Entry.vault_id`.
- `Ledger.blob()` as a resolver: local blob → vault → original path → `None`.
- `Ledger.whereabouts()` — where the bytes are and whether they're reachable now.
- `Bag.attach_vault()` / `detach_vault()` / `unreachable()`; vaults persist in config.
- `hermione add --vault NAME`, `hermione vault`, `hermione unreachable`.

**What it unlocks, without new code**

- **Search a drive that isn't plugged in** — now the default behaviour, not a feature.
- **The shadow index** is just a bag whose vault is empty. No new concept.
- **Multiple vaults** — "that's on the black SSD."

---

## Iteration 1c — corpus journal
*Files: `corpus.py` (new), `runtime.py`, `cli.py`*

**Closes:** `status` was a snapshot, and every question people actually ask a personal archive is a question about change.

**Added**

- `CorpusJournal` — append-only `corpus.jsonl`, one line per ingest/consolidate/dream.
- Named time windows (`today` … `ever`), `summarise(window)`.
- `hermione history -w week`.

Cheapest thing in the system; it's what makes 1d possible at all.

---

## Iteration 1d — the voice
*Files: `ask.py` (new), `cli.py`*

**Closes:** the bag was a set of commands, not one thing you talk to.

```
hermione ask "Hermione, remind me what I added this week"
hermione ask "what have I been into lately"
hermione ask "what am I missing"
hermione ask "what's on the black drive"
```

**Added:** intents `additions · interests · gaps · whereabouts · history · size · versions`, a greeting stripper, and a registry so new intents are one decorated function.

**The load-bearing decision: no model.**

This is a deterministic intent router. The bag's whole claim on your trust is that it doesn't make things up; a stochastic layer on top of an honest index would inherit none of that and spend all of it. If a question doesn't match a known shape, Hermione says so plainly and searches instead — falling back is always defensible, guessing never is.

An LLM belongs here eventually as a **better parser** — phrasing → intent — and never as the answerer. The answers stay computed. That boundary should survive every future iteration.

**Two bugs found and fixed during this iteration, both worth recording:**

1. *"10 searchs", "1 are revisions".* Trivial, and corrosive: this is the surface that's supposed to feel like one thing you talk to, and a single mis-inflected word undoes it.
2. *Taught codewords were reported back as failures.* `gaps` first classified any query with no literal match as a hole — which includes every codeword you deliberately taught it. The first fix compared the top score against the spread of all scores, and was wrong for a subtle reason: in any 60-sample distribution something is always two standard deviations out, so it measured coincidence rather than learning. The correct signal is **`w_fast` alone** — it's exactly zero until an intron is reinforced, so a non-trivial cosine there means *someone taught this*, which is precisely the question being asked. `gaps` now separates real holes from learned answers.

---

## Not yet built

Carried forward, in rough priority order:

| | from | note |
|---|---|---|
| Lineage-aware ranking | 1a rough edge | prefer heads, collapse superseded versions |
| `hermione watch DIR` | Vault/TextDB | ambient ingest; fits the metabolism |
| Clipboard capture | Vault/TextDB ("catching fireflies") | best value-per-line available |
| `hermione pack` + zipapp + `FORMAT.md` | Vault/TextDB (`vaultdb-run`) | the 20-year story: put the reader on the drive |
| Intron graph | exploration doc | introns pointing at introns, grown from misses |
| VSA introns | exploration doc | compose by addition; merging bags becomes a CRDT |
| Learned chunking | Gen 1 `chunk.py` | the introns decide where the introns go |
| MCP server / FUSE mount | product dream | agent memory; `~/mind/boat/` as a folder that isn't there |
| Calm visual surface | this iteration's ask | timeline of what arrived, towers drifting |

## Removed so far

Nothing. When something goes, it gets a row here with the reason.
