# NTDB: Analysis, Expansion, and a Falsifiable Test Plan

**Date:** 2026-08-07
**Scope:** Restate the architecture without metaphor, audit the six load-bearing claims, expand the underdeveloped ideas, and specify a test battery + MVP ladder that could actually kill or confirm the design on one laptop.

---

## Part 0 — What NTDB mechanically is

Strip the biology and here is the whole system:

> An append-only file of `[1KB metadata header][variable data block]` pairs. Each header carries a 256-float vector. A text query is hashed into a 256-float vector. Dot-product the query vector against every header's vector, read the top-K blocks, scan them for a match, then nudge the vectors of the blocks you read toward or away from the query vector.

That's it. Everything else — towers, lighthouses, the hippocampus framing — is either a filter predicate over headers (towers), a second layer of the same mechanism (lighthouses), or narration.

This is not a criticism. Reducing it to one paragraph is what makes it testable. But it means the interesting questions are narrower and sharper than the conversations so far have framed them:

1. Can a *model-free* hash encoder produce enough signal for the learning rule to generalize beyond queries it has literally seen?
2. Does routing at *column granularity* buy anything over what columnar formats already do for free?
3. Does the learning rule converge, or collapse?

Everything worth building or testing hangs off those three.

---

## Part 1 — Claim audit

Six claims are doing the heavy lifting across your documents. Three hold up with revision, two are wrong as stated, one is a category error.

### 1.1 "Similar queries produce similar vectors" — **wrong as written, and it's the critical one**

From the paper, §3.1:

```python
h = sha256(text.encode()).digest()
return np.frombuffer(h, dtype=np.uint8).astype(float32) / 255.0
```

annotated with *"Locality: Similar queries produce similar vectors."*

SHA-256 is an avalanche function. Flipping one input bit changes ~50% of output bits by design. `"alice email"` and `"alice emails"` are as far apart as `"alice email"` and `"tectonic uplift"`. There is **zero** locality. (A second, smaller bug: a SHA-256 digest is 32 bytes, so this yields a 32-d vector, not 256-d. The variant in the older chat — seed an RNG with the hash, draw 256 Gaussians — fixes the dimension but not the locality.)

The consequence is severe and specific. With no locality in the encoder, the *only* way an intron can respond to a novel query is if that query's random vector happens to correlate with accumulated weight — which, for orthogonal random vectors in 256-d, it does not. **The system degenerates into a lossy hash table of previously-seen query strings.** It will look like it's learning in any benchmark where you train and test on the same query set. It will collapse on held-out queries.

This is why Test T1 below is the single most important experiment, and it's cheap.

**The fix, and it's the highest-leverage change in this document:** don't hash the query string. Hash its *features*.

```python
def encode(text, d=256):
    v = np.zeros(d, dtype=np.float32)
    for tok in features(text):          # words + char 3-grams
        h = blake2b(tok.encode(), digest_size=8).digest()
        idx  = int.from_bytes(h[:4], 'little') % d
        sign = 1.0 if h[4] & 1 else -1.0    # signed hashing kills bias
        v[idx] += sign
    n = np.linalg.norm(v)
    return v / n if n else v
```

This is the **hashing trick** (Weinberger et al., ICML 2009), with the sign trick that makes the collision noise zero-mean. Now `"alice email"` and `"alice address"` share the `alice` coordinate and have genuine cosine similarity. Character 3-grams additionally give you fuzzy matching on typos and morphology for free.

Crucially this preserves the property you actually care about: **no embedding model, no 5GB download, runs on an M1 Air.** It costs ~20 microseconds per query. You lose nothing and gain the entire premise of the system.

Related: your instinct in the chat that the intron creates similarity between "coffee" and "tea" by sitting between them is correct *in principle* — that's co-occurrence learning — but it only works for terms the intron has actually co-observed. It cannot generalize to a third term. Feature hashing gives you the surface-form generalization; intron co-occurrence gives you the semantic generalization. You want both layers, and the documents currently only have the second.

### 1.2 "Converged: O(1)" — **wrong; the real win is elsewhere**

Scoring dot-products every intron on every query. That is O(M), always, forever. There is no state in which it becomes O(1). The paper's own §5.2 says "Score all M introns: O(M)" and then §4 claims O(1) — an internal contradiction worth fixing before anyone else reads it.

But the honest version of this claim is still good, and you should switch to it:

> **The win is I/O amplification, not complexity class.** Scoring is a cheap O(M) matmul over data that fits in L3 cache. Reading and scanning blocks is the expensive part, and *that* drops from O(M) blocks to O(K) blocks with K → 1–3.

State it as *blocks read per query at fixed recall*, and it's both true and impressive. State it as O(1), and the first database person who reads it stops reading.

The lighthouse layer is the only thing that could make scoring genuinely sublinear, and only if lighthouses *partition* the intron set rather than merely re-rank it. That's a real design decision currently unmade in your documents: **are lighthouses a pruning tree or a scoring ensemble?** They're described as both. Pruning gives sublinearity and risks missing results; ensembling is safe and stays O(M). Pick deliberately, ideally pruning-with-fallback.

### 1.3 The storage overhead table — **the comparison is not apples-to-apples**

§4.2's headline: NTDB has 20,000× less index overhead than a traditional DB at 1M rows.

The problem: a B-tree indexes **rows**. An intron indexes a **column**. They answer different questions. A B-tree on `users.email` answers "which row has `alice@x.com`" in ~3 I/Os. An intron on `users.email` answers "the email column exists and lives at byte 91,238" — and then you still linearly scan the entire column to find Alice.

So the 20,000:1 ratio is comparing an index that does the work against an index that doesn't. Anyone from the database world will spot this immediately, and it will cost you credibility on the claims that *are* sound.

**The correct comparison class is zone maps / row-group statistics** — Parquet already stores per-column-chunk min/max/null-count/distinct-estimates at roughly the same constant-per-chunk overhead you're proposing, and uses them for exactly this: skipping blocks without reading them. Parquet is your real baseline, not PostgreSQL.

And once you make that comparison, the genuinely defensible claim appears:

> Zone maps only work when the data has exploitable order (min/max on sorted numerics). They are useless on unsorted text, blobs, mixed-type heterogeneous dumps, and cross-column semantic queries. **NTDB replaces statistical zone maps with learned ones, which work where min/max carries no information.**

That is a real gap in the state of the art, it's narrow, it's honest, and it's defensible. It's a much better pitch than "20,000× better than a B-tree."

### 1.4 The learning rule — **structurally unstable in three specific ways**

```python
lr = 0.01 if found else -0.001
intron.weights += lr * query_vec
intron.weights *= 0.999
```

Three failure modes, all predictable, all fixable:

**(a) Norm blowup / "loud intron" collapse.** Score is a raw dot product, so `score = ||w|| · ||q|| · cos(θ)`. An intron that gets queried often accumulates a large `||w||` and starts winning on *magnitude* rather than *direction* — it outranks better-matching introns on every query. Under a skewed (Zipf) query distribution, which is every real workload, you get rich-get-richer collapse: one or two introns dominate the top-K for all queries, and the index becomes actively worse than random.

The `*= 0.999` decay does not save you. Solving the fixed point of `w ← (w + lr·q)·decay` for aligned updates:

```
||w||_eq = decay·lr·||q|| / (1 − decay) = 0.999 × 0.01 / 0.001 ≈ 9.99 · ||q||
```

So decay *does* bound the norm — but at ~10× the query norm, and only for introns that get touched. Decay is applied **only to introns in the top-K**, so an intron touched constantly converges to ~10‖q‖ while one touched rarely stays near its small random init. The ratio between them diverges by touch frequency, which under a Zipf workload is exactly the runaway you don't want.

*Fix:* score with cosine similarity, or L2-normalize weights after each update. One line. This is probably the difference between the prototype working and not working.

**(b) Cold-start lock-in.** Only the top-K introns get read, verified, and updated. An intron whose random initialization puts it outside the top-K for every early query is **never read, therefore never updated, therefore never enters the top-K.** It is permanently invisible. Its data is unreachable through the learned path forever.

This is not a corner case — it's the default behavior for most of the corpus. It's also exactly the explore/exploit dilemma, which means the right frame is:

> **This is a linear contextual bandit, not supervised learning.**

Naming it correctly hands you the entire literature: ε-greedy as a floor, LinUCB or Thompson sampling as the proper solution, and regret bounds that give you actual guarantees instead of §11.3's open questions. LinUCB in particular is a near-perfect fit — it maintains exactly a weight vector plus a covariance matrix per arm, adds an optimism bonus for under-explored arms, and is ~15 lines over what you already have. Your open questions #4 (catastrophic forgetting) and #5 (adversarial queries) are both answered in that literature.

*Minimum fix:* with probability ε=0.1, include a random intron in the candidate set. Decay ε over time.

**(c) Learning from unverified signal.** `found` is computed by `_matches()`, a substring scan. If verification is fuzzy or absent, the reward signal is noise and the weights random-walk. Keep the reward strictly tied to a deterministic verification — which leads directly to the best idea in your pile, below.

### 1.5 "The LLM's thought vector becomes the query, no translation layer" — **category error, but the repaired version is stronger**

An LLM's hidden state lives in a learned basis specific to that model. Your intron weights live in the hash-feature basis. They are different vector spaces that happen to have similar dimensionality. Feeding GPT activations into a dot product against hash-basis intron weights produces noise. There is no "same language."

What you *can* do — and it's genuinely valuable — is learn a projection:

```
P: R^d_model → R^256,   trained contrastively on (query_hidden_state, correct_intron)
```

This is one linear layer. Maybe 400K parameters. Trains in minutes on a laptop from query logs.

So the honest claim is not "no translation layer." It's:

> **NTDB replaces a brittle *symbolic* translation layer (text→SQL, function calling, agent scaffolding) with a *differentiable* one.**

Which is still a large claim, and unlike the original it's true. Symbolic translation fails discontinuously — one wrong column name and the query errors out. A learned projection degrades gracefully and improves with feedback. That's the actual argument against RAG-as-currently-practiced, and it's much harder to dismiss.

The deeper point buried in your chats and worth pulling to the front: **conventional retrieval indexes are not differentiable.** BM25, HNSW, B-trees — you cannot backpropagate retrieval error into them. You can into an intron layer. That means the index can be trained *jointly with the generator* on end-task loss. That is the real novelty seed.

Note the prior art here: [Transformer Memory as a Differentiable Search Index](https://arxiv.org/abs/2202.06991) (Tay et al., NeurIPS 2022) does exactly this by memorizing document IDs inside transformer parameters. Your distinction is real and worth stating explicitly: DSI puts the learned index *in the model* (so it can't scale past the model, and adding documents requires retraining — see DSI++ for how painful that is); NTDB puts it *on disk beside the data* (so it scales with storage and new data is an append). That's a genuine architectural difference in your favor, and framing it as "DSI, but the parameters live in the file system" is a far sharper pitch than "kills RAG."

### 1.6 Novelty — **real, but narrower than claimed, and you're missing your closest ancestor**

Correctly cited in the paper: learned indexes (Kraska 2018), learned Bloom filters (Mitzenmacher 2018).

**Missing and important: [Database Cracking](https://www.cidrdb.org/cidr2007/papers/cidr07p07.pdf) (Idreos, Kersten, Manegold, CIDR 2007).** Core thesis: *index maintenance should be a byproduct of query processing, not of updates.* Each query is treated as advice about how to physically reorganize storage. Structure emerges from the query stream. That is your philosophy, stated 19 years earlier, and it [won the CIDR 2026 Test-of-Time award](https://www.cwi.nl/en/news/tidying-up-while-you-search-cwi-paper-database-cracking-wins-cidr-test-of-time-award/) — six months ago.

This is good news, mostly. It means the direction is validated by the field's own retrospective judgment rather than being a lone-crank idea. But you must know it, cite it, and be able to say precisely how you differ. Cracking physically *reorders* data within a column and produces exact results; NTDB leaves data immutable and learns a *probabilistic router* over heterogeneous blocks. Different mechanism, same instinct.

Also relevant and uncited: adaptive/stochastic cracking (Halim et al., VLDB 2012), which addresses exactly the workload-skew robustness problem you'll hit in T2/T7.

**What actually remains novel, stated tightly:**

> Query-driven learned routing over heterogeneous schema-less blocks, where routing metadata is constant-size per block regardless of row count, learned online from a model-free feature encoder, with no offline training phase and no embedding model dependency.

Narrow. Real. Publishable if the numbers hold.

---

## Part 2 — Expanding the ideas worth expanding

### 2.1 The hybrid intron — the best unexploited idea in your notes

In the older chat you proposed a parser that is "half deterministic, half a small focused CNN." That instinct is right and under-built. Here's the sharp form:

**The learned vector proposes. A Bloom filter disposes.**

An intron is 1KB. Currently: 256 floats (1024B) + metadata, which already overflows the budget. Reallocate:

| Field | Bytes | Purpose |
|---|---|---|
| offset, size, flags | 24 | physical |
| column name | 48 | semantic |
| tower tags | 48 | logical |
| **learned weights (128 × f32)** | **512** | **recall / routing — fuzzy** |
| **Bloom filter (k=4)** | **320** | **precision / verification — exact** |
| checksum, timestamps, counters | 72 | integrity + stats |
| **total** | **1024** | |

Now the query path is:

1. Score all M introns by cosine on the learned half → candidate set (fuzzy, high recall, *may* be wrong)
2. Probe the Bloom filter of each candidate → discard definite-nots at **zero I/O** (exact, no false negatives)
3. Read and scan only survivors

The Bloom filter has zero false negatives, so step 2 can only remove blocks that provably don't contain the term. Its false-positive rate for a 320-byte (2560-bit) filter, computed properly:

| distinct tokens/block | optimal k | false positive rate |
|---|---|---|
| 100 | 18 | ~0.00% |
| 200 | 9 | 0.21% |
| 300 | 6 | 1.66% |
| **500** | **4** | **8.64%** |
| 800 | 2 | 21.6% |
| 1200 | 1 | 37.4% |

**This table is itself a design constraint, and a sharper one than I expected.** The Bloom half only works if blocks stay under ~300–500 distinct tokens. Past that it degrades fast and becomes worthless by ~1000. That directly couples **block size to intron budget** — you cannot make blocks arbitrarily large "because storage is cheap," because the verification layer dies. Feed this into T5; it may be the binding constraint on the whole physical layout.

Within that regime (≤300 distinct tokens/block), ~98% of the learned layer's mistakes get caught before touching the disk.

This matters more than it looks. §1.4(c) said the reward signal must be verified; the Bloom filter *is* cheap verification. It converts the noisy `found/not-found` signal into a clean one, at 320 bytes, without reading data. It also gives you a graceful degradation story: **on a cold database with untrained weights, the Bloom filters alone already work.** The cold-start weakness you identified — "a brand new NTDB is dumb" — largely disappears. The system starts as a competent Bloom-filtered scanner and *improves* into a learned router, instead of starting useless.

Halving the weight vector to 128-d costs you almost nothing: with feature hashing, 128 dimensions is ample for the number of distinct routing decisions at M ≤ 10⁶, and it halves your scoring cost. §11.3's open question "why 256?" has an empirical answer available in one afternoon (T5).

### 2.2 The sleep cycle — cheap, and it builds your lighthouse layer for free

Your "LLM dreams inside the database" idea is the most fun and, stripped of the LLM, the most immediately buildable. It needs no model at all:

```
nightly:
  1. CLUSTER    k-means over intron weight vectors → k clusters
  2. PROMOTE    each centroid becomes a Lighthouse; members become its towers
  3. MERGE      introns with cos(w_i, w_j) > 0.95 and same column → merge blocks
  4. REPLAY     re-run a sample of the historical query log; any query whose
                recall regressed rolls back the offending weight update
  5. PRUNE      introns with hit_count = 0 after N queries get their weights
                re-randomized (escape from cold-start lock-in)
```

Four of these are twenty lines of numpy. Step 2 is the important one: **lighthouses currently have to be hand-defined in all your code sketches, which is a schema by another name and quietly contradicts the "no schema" premise.** Clustering makes them emergent, which is what the architecture claims they are.

Step 4 is your answer to catastrophic forgetting (§11.3 #4) — it's experience replay, standard practice, and it turns "does it forget?" from an open question into a bounded, measurable one.

### 2.3 Where the enterprise pitch is wrong, and where it's right

The legal-discovery example in your chats is the **worst** possible enterprise entry point. E-discovery operates under legally mandated recall standards; producing a probabilistic index that might silently miss a responsive document is not a feature, it's a sanctionable liability. Never lead with it.

Fits where probabilistic recall is *acceptable* and the alternative is *nothing*:

- **Cold-tier log/archive triage.** Splunk keeps 30 days; three years sit in S3 unsearchable. NTDB over the cold tier competes against grep and despair, not against Splunk. Missing 5% of matches is fine when the status quo is 100% unsearchable. This is your Trojan horse, and the framing in your chat was correct.
- **Agent working memory.** An autonomous agent's scratch memory has no schema, grows organically, is queried fuzzily, and tolerates imperfect recall. Nearly ideal fit, and it's the fastest-growing new workload in the industry.
- **Personal knowledge / the Memex case.** Genuine, but it's a $0 market. Great for adoption and demos, not for revenue.

Fits where it structurally cannot win: OLTP, anything with a recall SLA, point lookups on known keys (a B-tree does it in 3 I/Os, exactly, forever — do not fight this), and anything requiring multi-writer consistency.

### 2.4 A naming/positioning note

"Neural Tower Database" invites the reader to expect a neural network and then find a dot product, which reads as overclaiming. The mechanism's actual honest description — *learned routing metadata co-located with data blocks* — is more impressive to the audience that matters than the biology framing. Consider leading with the mechanism and keeping the biology as an aside. Also: the paper's §5.3 latency/accuracy table is labeled "empirical observations from prototype implementation." If that prototype doesn't exist, those numbers must come out before anyone else sees the document. Fabricated benchmarks are the one error that ends a project's credibility permanently, and it would be a shame here because the real numbers are obtainable in a weekend.

---

## Part 3 — The stress-test battery

Each test: the claim, what would falsify it, the metric, the pass bar, and my prediction. Ordered by information-per-hour.

**Headline metric for everything: blocks read per query at fixed recall@10.** Not latency. Latency on a laptop in Python measures Python, and it will hide the effect you're looking for under interpreter overhead.

---

### T0 — The honesty harness (build this first, before any NTDB code)

**Why first:** without baselines, every subsequent number is unfalsifiable and you will fool yourself.

Same corpus, same query set, four systems:

| System | Role |
|---|---|
| linear scan / `grep` | the floor — NTDB must beat this or the project is over |
| SQLite FTS5 | the "just use the boring thing" control |
| Parquet + DuckDB (zone maps) | **the real competitor** (see §1.3) |
| NTDB | the subject |

Measure: blocks/bytes read, wall time, recall@10, index bytes on disk.

**Pass bar:** NTDB beats linear scan on bytes-read by ≥10× after training, and stays within 2× of Parquet's wall time.
**Prediction:** NTDB beats grep comfortably. Loses to FTS5 on precision, which is fine and expected. The Parquet comparison is the one that will teach you something.

---

### T1 — Memorization vs. generalization ⭐ **the decisive test**

**Claim:** the system learns query *patterns*, not query *strings*.
**Method:** partition queries into train set A and held-out set B from the same distribution, disjoint strings. Train on A. Evaluate on A, on B, and on random unrelated queries C.
**Metric:** blocks-read on each.
**Pass bar:** `B` must land closer to `A` than to `C`. Specifically: (B − A) / (C − A) < 0.5.

**Prediction, stated in advance so it counts:**
- With SHA-256 whole-string hashing → **B ≈ C. Total failure.** The system is a hash table.
- With signed feature hashing (§1.1) → **B lands roughly 60–75% of the way to A. Passes.**

If both fail, the core premise is dead and you've learned it in one evening for ~200 lines of code. If feature hashing passes and SHA fails, you've converted a fatal flaw into a solved problem and you have your first real result. **Run this test before writing anything else.**

---

### T2 — Loud-intron collapse

**Claim:** the learning rule is stable under realistic (skewed) workloads.
**Method:** Zipf-distributed query stream, 100K queries. Track the distribution of `||w||` and the entropy of the top-1 intron across queries.
**Pass bar:** top-1 entropy stays within 20% of its initial value; `max||w|| / median||w||` < 3.
**Prediction:** raw dot product **fails hard** — a handful of introns capture the top-K for nearly everything by query ~20K. Cosine scoring passes. This is a one-line fix confirmed by a one-hour test, which is the best ratio in the whole battery.

---

### T3 — Cold-start lock-in

**Claim:** all data remains reachable through the learned path.
**Method:** after 50K queries, what fraction of introns have *ever* been in a candidate set?
**Pass bar:** > 95% coverage.
**Prediction:** pure greedy top-K reaches maybe 30–50% and stalls. ε-greedy (ε=0.1, decaying) reaches >95%. Combined with the Bloom filter (§2.1) coverage should be near-total, because Bloom probing is free and can cover the whole intron set regardless of what the learned layer proposes.

---

### T4 — Scoring scale curve

**Claim:** "10,000 introns is a snack; a million is fine."
**Method:** M ∈ {10³, 10⁴, 10⁵, 10⁶}, measure scoring time (numpy matmul), resident memory, end-to-end query time.
**Pass bar:** scoring stays <10ms at M=10⁶ on M1.
**Prediction:** **passes easily and this is your strongest result.**

| M | d=128 | d=256 |
|---|---|---|
| 10³ | 0.5 MB | 1.0 MB |
| 10⁴ | 5.1 MB | 10.2 MB |
| 10⁵ | 51 MB | 102 MB |
| 10⁶ | 512 MB | 1.0 GB |

At M=10⁶, d=128 that's 0.26 GFLOP per query against ~100 GB/s M1 unified memory — memory-bandwidth-bound at roughly 5ms. Your intuition that "10,000 introns is a snack" is correct, and it holds three orders of magnitude further than that. Note d=128 halves both memory and time for (per T5) probably no measurable quality loss. Expect scoring to be a rounding error next to block I/O, which is exactly what sets up T5.

---

### T5 — Verification cost ablation (finds your key engineering constant)

**Claim:** vector routing is where the win comes from.
**Method:** sweep block size 4KB → 4MB and weight dimension d ∈ {32, 64, 128, 256, 512}. Measure the fraction of query time in scoring vs. block read vs. block scan.
**Pass bar:** find the regime where routing dominates the win. If block-scan time swamps everything at all block sizes, the vector layer is decoration.
**Prediction:** there's a sweet spot around 64KB–512KB blocks. Below it, intron overhead dominates (1KB per 4KB block = 25% overhead — unacceptable, and it quietly breaks the §4 storage argument). Above it, verification scanning swamps the routing win. **Also settles "why 256 dimensions?" empirically — I expect 64–128 to be indistinguishable from 256 and twice as fast.**

---

### T6 — Hybrid intron A/B

**Claim:** §2.1 — a Bloom half beats an all-vector intron at equal byte budget.
**Method:** 256-float intron vs. 128-float + 320-byte Bloom, identical 1KB budget.
**Metric:** wasted block reads (false positives), and cold-start performance at query #1.
**Prediction:** hybrid wins decisively, especially cold — probably 10–20× fewer wasted reads before training, converging to a smaller but persistent advantage after. If this holds it becomes the headline architectural result and reshapes the paper.

---

### T7 — Distribution drift and forgetting

**Claim:** continuous online learning is robust (paper §11.3 open question #4).
**Method:** train on distribution P1 → switch to P2 → switch back to P1. Measure recovery time in queries.
**Pass bar:** recovery to within 20% of original P1 performance in <5K queries.
**Prediction:** without replay, slow and incomplete recovery. With sleep-cycle replay (§2.2), fast. This test converts an open question into a number.

---

### T8 — Adversarial weight poisoning

**Claim:** paper §11.3 open question #5.
**Method:** inject a query stream designed to drag one intron's vector to dominate, then measure damage to legitimate query performance.
**Pass bar:** legitimate performance degrades <10% under 5% poisoned traffic.
**Prediction:** unnormalized weights are **trivially poisonable** — this is the same mechanism as T2, just deliberate. With cosine + per-update clipping + learning only on Bloom-verified hits, it's largely mitigated. Worth doing because "we thought about the attack surface" is table stakes for the enterprise story, and because the mitigation (per-user weight deltas) turns out to double as a *feature*: personalized index layers over shared data, which is also your access-control story from the Cortex sketch.

---

### T9 — The real-corpus test (the one that tells you if it *feels* like anything)

**Method:** point it at a genuinely messy real folder. Your own `NTDB/` folder is a legitimate specimen — markdown, txt, chat exports, mixed English and Norwegian, wildly inconsistent structure. Better: a few GB of your actual documents.
**Metric:** subjective, plus recall@10 against hand-labeled queries.
**Why it matters:** T1–T8 tell you if the mechanism works. T9 tells you if the *product* works. Synthetic benchmarks with injected patterns are notoriously easy to accidentally rig — the pattern injection creates exactly the structure the learner is designed to find. Real data doesn't cooperate.
**Prediction:** this is where the mixed-language, mixed-format heterogeneity either shows the value proposition or reveals it as grep with extra steps. Genuinely uncertain, which is what makes it worth running.

---

### T10 — Differentiable retrieval head

**Claim:** §1.5 — retrieval error can be backpropagated into the storage layer.
**Method:** small sentence-transformer → learned linear projection to d=128 → intron scoring → contrastive loss on (query, correct block) pairs. Compare against the hash encoder.
**Pass bar:** beats feature hashing on held-out queries by a margin that justifies the model dependency.
**Prediction:** wins on quality, loses on the "no model needed" property. The interesting result is the **gap size** — if feature hashing gets 80% of the benefit at 0% of the dependency, that's a strong argument for the model-free design, which is your differentiator. Run last; it's the only test that needs a model.

---

## Part 4 — MVP ladder

Each rung is scoped for one person on an M1 Air, and each has an explicit **kill criterion** — the result that should make you stop rather than push forward on sunk cost.

### MVP-0 · "Does it learn?" — one evening, ~300 lines

In-memory only, no file format, synthetic corpus. Implements: feature-hash encoder, cosine scoring, ε-greedy candidates, weight update.
**Runs:** T1, T2, T3.
**Kill criterion:** if T1 fails with feature hashing — if held-out queries perform no better than random ones — **the core premise is false and you stop here.** One evening spent to test the load-bearing assumption is the best trade in this document.

### MVP-1 · "Honest numbers" — a weekend

Adds the on-disk format, Bloom-hybrid introns, and the T0 baseline harness.
**Runs:** T0, T4, T5, T6.
**Kill criterion:** if it can't beat Parquet zone maps on heterogeneous data — the one place zone maps are supposed to be blind — the niche doesn't exist. Reconsider.
**Output:** the first legitimately publishable numbers, and enough to replace §5.3's placeholder table with real data.

### MVP-2 · "Chaos engine" — one week

`ntdb ingest ~/Documents && ntdb find "coffee meeting alice"`. Ingest adapters for txt/md/pdf/csv/json. A CLI. A learning curve plot.
**Runs:** T9.
**Kill criterion:** if you personally stop reaching for it after two weeks of having it installed, no one else will either.
**Why this rung matters most for adoption:** this is the demo. "I pointed it at my messy folder and it got better at finding my stuff" is a story people repeat. Learning curves are not.

### MVP-3 · "Sleep cycle" — one week

Offline consolidation: k-means → auto-lighthouses, merge, replay, prune (§2.2).
**Runs:** T7, T8.
**Kill criterion:** if auto-generated lighthouses don't beat flat intron scoring, the three-layer architecture is one layer too many — and simplifying to two layers is a *good* outcome, not a failure.

### MVP-4 · "Differentiable" — two weeks

Projection head, joint training, agent-memory integration.
**Runs:** T10.
**Kill criterion:** if the learned projection doesn't beat feature hashing by a wide margin, drop it — the model-free property is worth more than a marginal quality gain, and it's your actual differentiator against every vector DB on the market.

---

## Part 5 — The shortest path

If you do one thing: **MVP-0 and T1.** One evening, ~300 lines, no dependencies beyond numpy.

It tests the single assumption everything else rests on — that the encoder provides enough locality for the learning to generalize — and it will either kill the project cheaply or give you the first real evidence that the idea works. Both outcomes are worth far more than more architecture.

My honest read: the SHA-256 encoder as specified fails T1 and would have quietly poisoned every benchmark you ran, because train/test contamination in a synthetic harness is nearly invisible. Feature hashing fixes it. That single change, plus cosine scoring and ε-greedy, is the difference between a system that memorizes and a system that learns — and it's about fifteen lines of code.

The design's genuine contribution is narrower than the conversations have framed it, but it is real: **constant-size learned routing metadata co-located with heterogeneous data blocks, trained online without an embedding model.** That's a defensible claim, it sits in a gap the field hasn't filled, and it's testable on a laptop in a weekend.

---

## References

1. Idreos, Kersten, Manegold. "Database Cracking." CIDR 2007. — [paper](https://www.cidrdb.org/cidr2007/papers/cidr07p07.pdf) · [CIDR 2026 Test-of-Time award](https://www.cwi.nl/en/news/tidying-up-while-you-search-cwi-paper-database-cracking-wins-cidr-test-of-time-award/)
2. Tay et al. "Transformer Memory as a Differentiable Search Index." NeurIPS 2022. — [arXiv:2202.06991](https://arxiv.org/abs/2202.06991)
3. Weinberger et al. "Feature Hashing for Large Scale Multitask Learning." ICML 2009. — the hashing trick
4. Kraska et al. "The Case for Learned Index Structures." SIGMOD 2018.
5. Mitzenmacher. "A Model for Learned Bloom Filters." NeurIPS 2018.
6. Halim et al. "Stochastic Database Cracking: Towards Robust Adaptive Indexing." VLDB 2012. — [paper](https://dl.acm.org/doi/abs/10.14778/2168651.2168652)
7. Li et al. "A Contextual-Bandit Approach to Personalized News Article Recommendation." WWW 2010. — LinUCB
8. Charikar. "Similarity Estimation Techniques from Rounding Algorithms." STOC 2002. — SimHash
