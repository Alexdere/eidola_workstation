# Vault/TextDB, fed back into Hermione

**Date:** 2026-08-07
**Source:** *Vault and TextDB architecture* (Oct 23), read against the built stack.
**Mode:** Exploration. Looking for what the old document solved that the new one hasn't noticed yet.

---

## Part 0 — The one sentence that matters

> *"The Vault is pure presence, the TextDB is pure reflection."*

Hermione has a fracture between **truth and derived** (Ledger vs. Clerk/Cortex). The old document has a *different* fracture, along a different axis: **presence vs. reflection** — heavy bytes that must be physically somewhere, versus a light distillate that can be everywhere.

These are orthogonal. Hermione currently collapses the second one, and it's the more consequential of the two.

Right now `Ledger.add_file()` copies every blob into the bag. That's correct for text and fatal for a real personal dump — the first 40GB of photos and video ends the story. Worse, it makes the bag *heavy*, which quietly kills the properties the Product Dream doc was most excited about: emailable, git-committable, roaming, "search a drive that isn't plugged in."

The old document already worked this out, and it has the piece that was missing:

> *"TextDB can remember what the Vault looked like last time it synced, but never depends on it."*

That's `vaultinfo`. The record of what exists somewhere else. Hermione's Ledger has no way to say **"I know this file exists, I know what's in it, I don't have the bytes right now."**

That's the gap. Everything below follows from it.

---

## Part 1 — The graft that reorganises everything

### Split the Ledger along the presence axis

The Ledger is currently doing two jobs that want to travel separately:

```
  Ledger today                      Ledger split
  ------------                      ------------
  journal.jsonl  (light)   ---->    Ledger    journal, ~200 bytes/entry, ALWAYS present
  blobs/         (heavy)   ---->    Vault     the actual bytes, MAY BE ABSENT
```

Then the correspondence to the old document is exact:

| Old doc | Hermione |
|---|---|
| Vault (authoritative, heavy, offline) | `vault/` — blobs, possibly on another drive, possibly gone |
| TextDB (derived, light, roaming) | Ledger journal + Clerk + Cortex |
| `vaultinfo` (what the Vault held last sync) | the journal itself, once presence is a field |

**Concretely, one new field on `Entry`:**

```python
presence: str      # "here" | "vaulted" | "pending" | "missing"
vault_id: str      # which vault holds it -- "black-ssd", "s3://...", ""
```

and `Ledger.blob()` becomes a resolver that tries: local blob → mounted vault → *tell the user which drive to plug in*.

### What falls out of that one change

- **Search a drive that isn't plugged in.** The Product Dream doc listed this as a headline feature. With presence tracking it's not a feature you build, it's the default behaviour. You get told the file exists, what it's about, and that it's on the black SSD.
- **The shadow index stops being a separate concept.** It's just a bag whose vault is empty. No new code, no new format.
- **Multiple vaults.** `vault_id` per entry. "That's on the drive in the drawer." The old document assumed one Vault; there's no reason for that.
- **The bag becomes portable again.** Journal + Clerk + Cortex for a 10TB corpus is a few GB — emailable, syncable, versionable, exactly as the old doc wanted for TextDB.
- **`hermione add --no-copy`** becomes coherent rather than dangerous, because "I only have a reference" is now a *represented state* rather than a silent lie.
- **iCloud problem partly dissolves.** The heavy vault can live in the synced folder; the SQLite-bearing bag stays local. That's the workaround from the README turned into architecture.

Cost: roughly 60 lines in `ledger.py`, a resolver, and a `presence` column. Small, and it gets much more expensive to retrofit later once bags exist in the wild.

---

## Part 2 — The best single idea in the document

Buried in the Chrome-extension sketch, not in the architecture section:

```
hash          (blake3 of content)
parent_hash   (if it's an edit)
```

> *"This is enough to reconstruct your thinking over time — not just what you wrote, but how it changed."*

Hermione is currently **amnesiac about edits.** Content-addressing means an edited file is a brand-new `entry_id` with no relationship to its predecessor. `test_ledger_dedupes_and_survives_reopen` treats that as correct behaviour, and for dedup it is. But it means:

> Every time you edit a document, its intron is discarded and replaced by a fresh one seeded from content, with **zero** of the learning it had accumulated.

For a living corpus — the only kind a personal dump is — that's a slow leak of exactly the thing the system exists to accumulate. You teach it "the boat thing" means this file, you edit the file, and it forgets.

### The fix, and why it's better than it looks

Add `parent_hash` to `Entry`, and let new introns **inherit** rather than reinitialise:

```python
# today: every new chunk starts from its own content signature
w_slow[i] = encoder(chunk_text)

# with lineage: start from the predecessor's learned state
w_slow[i] = w_slow[parent_chunk]        # keeps everything it learned
w_fast[i] = w_fast[parent_chunk]
```

An intron becomes a **lineage** rather than a snapshot. What that unlocks:

- **Learning survives editing.** The single biggest practical win.
- **Query the past.** "The version of this before I rewrote it in March."
- **Diffs are first-class.** The Ledger already stores every version's bytes; lineage makes them navigable instead of a pile of near-duplicates.
- **Near-duplicate collapse.** A personal dump is full of `report_v2_FINAL_actual.docx`. Lineage detection (high cosine + high token overlap + adjacent time) merges them into one thing with a history, which is worth more than dedup.

Detecting the parent when the user just drops a file with no lineage info: the Cortex already has the machinery. Encode the new text, score against existing introns, and a chunk that's ~0.9 cosine with the same filename stem is almost certainly a revision. **The learned layer identifies its own lineage.** That's a nice closure — the index becomes useful for maintaining the index.

---

## Part 3 — Catching fireflies

> *"Instead of asking you to export or change behavior, the extension just listens."*
> *"For now, you're just catching fireflies."*

Hermione's ingest is `add`: pull-based, explicit, after the fact. The Echo sketch is **ambient capture**, and it carries an insight the current design misses entirely:

> The moment of writing is when the context is richest, and that context is unrecoverable afterwards.

A note captured at the moment of typing has `source_url`, timestamp, what you were editing, what preceded it. Drop the same text into a folder a week later and all of it is gone forever. No amount of clever extraction recovers it.

This argues for a distinction Hermione doesn't currently make: **capture-time metadata is precious in a way derived metadata is not.** It belongs in the Ledger next to the bytes, not in the derived layers — because unlike everything else in the Clerk and Cortex, it *cannot be rebuilt*.

Ingest modes worth having, in rough order of value:

- `hermione watch DIR` — filesystem watcher. Cheapest, most useful, no new surface. A daemon job that already fits the metabolism.
- **Clipboard capture** — a hotkey that drops whatever's on the clipboard in, with source app and timestamp. Maybe 40 lines on macOS. Absurdly high value-per-line for a "catch fireflies" tool.
- **Browser extension (Echo)** — the old doc's design, mostly still right. Note that it's now a *client* of a real backend rather than the whole system, which is a much easier build than what the doc imagined.
- **Share sheet / `Open with Hermione`** — the OS-native version of the same thing.

The "anti-librarian" principle from the Product Dream doc was about *organisation*. Echo extends it to *ingest*: don't ask the user to change behaviour there either. Same principle, one layer earlier, and the current design missed it.

---

## Part 4 — The drive that explains itself

> *"vaultdb-run: a portable executable. Drop it on any machine, plug the drive in, and it works without installation or dependencies."*

This is the strongest *product* instinct in the old document and Hermione has nothing like it.

The 20-year archive problem: a bag full of learned weights in a bespoke `.npz` layout is useless in 2046 if nothing can read it. The old doc's answer — **put the reader on the drive** — is correct and slightly radical.

```
/Vault/
    assets/                  the bytes
    bag/                     journal, clerk.db, cortex/
    hermione.pyz             zipapp: the whole reader, one file
    hermione                 shell wrapper
    FORMAT.md                plain-text spec of every file on this drive
    plugins/                 extractors that travel with the corpus
```

`hermione pack /Volumes/DRIVE` writes all of it. Then:

- Plug the drive into anything with Python. It works. No install.
- `plugins/` matters more than it looks: a vault full of some dead proprietary format can carry its own reader. The registry pattern already in `extract.py` is exactly the right shape for this — it was built for extensibility and turns out to also be the archival story.
- `FORMAT.md` is the real insurance. Even if every binary rots, a human can reconstruct the reader from the spec. That's the txtdb instinct from the Norwegian notes — *readable metadata as a hedge against opacity* — finally earning its keep.

The old doc wanted Rust or Zig for a static binary. Tempting, and wrong for now: it would cost the iteration speed that's currently the project's main asset. A Python zipapp gets ~90% of the portability at ~2% of the cost. Revisit when the format stops changing.

---

## Part 5 — Corpus history

`vaultinfo` holds *"counts, last hash set, missing or new items"*, and `sync.log` is JSONL of *"sync time, file count, hash count, new/deleted files."*

Hermione's `status` is a snapshot with no memory. Appending it to a `corpus.jsonl` on every ingest and consolidation is maybe 15 lines, and it turns several speculative ideas into working features:

- **"The database has a mood."** Weight drift over time = what the corpus has been about lately. Already computable from `w_fast` vs `w_slow`; just needs recording.
- **Absence detection.** *"You had this file in March. It's gone now."* Combine with the negative-space idea and you get a corpus that notices its own losses — which is a thing no file system will ever tell you.
- **The learning curve becomes visible.** `cortex_only` over months is the product's reason-to-exist metric plotted against time. That's the number worth watching, and right now it's only computable for the present moment.

Cheapest item in this document, and it makes several previously-speculative things concrete.

---

## Part 6 — What not to take

Being honest about where the old document would be a step backwards:

- **No learned layer at all.** The Vault/TextDB design is pure deterministic mirroring. Hermione is strictly ahead here; don't let the old architecture's cleanliness pull the Cortex out.
- **`text.db` stores whole documents; the Clerk stores chunks.** Chunks are right — they're the unit of retrieval and the unit an intron attaches to. Don't regress to document granularity.
- **Two-way sync as designed invites conflicts.** The old doc worried about Vault-pulls-from-TextDB. Append-only + content-addressing makes this a non-problem: there are no conflicting writes, only entries that haven't been promoted to a vault yet. One `presence: "pending"` state, no sync protocol.
- **The Apple Notes / app-link thread** is a dead end and the document itself concludes as much. Skip.
- **Rust/Zig rewrite.** Later, if ever.

---

## Part 7 — The merged shape

Both documents were circling the same structure from different sides. Put together:

```
        capture (drop · watch · clipboard · browser)
                       |
   +-------------------v--------------------+
   |  LEDGER   journal: what exists, where,  |   light · always present
   |           what it hashed to, lineage    |   emailable · git-friendly
   +----+-------------------------+---------+
        |                         |
   +----v-----+            +------v-------+
   |  VAULT   |            | CLERK  FTS5  |   exact, boring, teaches
   |  bytes   |            +------+-------+
   | may be   |                   |
   | absent   |            +------v-------+
   +----------+            | CORTEX       |   learned, fuzzy, inherits
                           | introns      |   across edits
                           +--------------+
```

Three independent failure domains, and the old document's slogan holds for all of them: **each component can die or travel independently.**

- Lose the Vault → you still know what you had, what it was about, and which drive it's on.
- Lose the indexes → rebuild from Ledger + Vault.
- Lose everything but the Vault → rebuild all of it.
- Lose everything but the Ledger → you have a shadow index: a searchable description of a corpus you no longer possess.

That last line is the interesting one, and neither document got there alone.

---

## Part 8 — What this costs

| Graft | Value | Code | Note |
|---|---|---|---|
| **`presence` + `vault_id` on Entry** | very high | ~60 lines | Gets expensive to retrofit later. Do first. |
| **`parent_hash` lineage + intron inheritance** | very high | ~80 lines | Stops the learning leak. The best idea in the old doc. |
| **`corpus.jsonl` history** | medium | ~15 lines | Cheapest thing here. |
| **`hermione watch`** | high | ~50 lines | Ambient ingest, fits the metabolism. |
| **Clipboard capture** | high | ~40 lines | Best value-per-line in the document. |
| **`hermione pack` + zipapp + FORMAT.md** | medium | ~120 lines | The 20-year story. Not urgent, very cheap insurance. |
| **Browser extension (Echo)** | medium | separate project | Much easier now that a real backend exists. |

The first two are the ones I'd argue for. They're both **architectural rather than featural** — cheap now, awkward later — and they close real gaps rather than adding surface:

1. **Presence** closes: the bag can't get heavy, and can't represent absence.
2. **Lineage** closes: the Cortex forgets everything it learned every time you edit a file.

The second is the one I'd fix first, because it's invisible. Nothing breaks, no error appears — the system just quietly stops accumulating the thing it exists to accumulate, and you'd probably never notice.
