"""Tests for the parts that are easy to get subtly wrong.

These are executable specifications of the design invariants, not coverage.
The decisive one is `test_cortex_learns_associations_exact_search_cannot`:
if that fails, the premise is wrong and the rest doesn't matter.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hermione.config import Config          # noqa: E402
from hermione.cortex import bloom, encoder  # noqa: E402
from hermione.runtime import Bag            # noqa: E402


# ---------------------------------------------------------------- encoder
def test_encoder_has_locality():
    """The whole design rests on this. A whole-string hash would fail it."""
    enc = encoder.get("hashing", dim=256)
    related = float(enc("alice email") @ enc("alice address"))
    unrelated = float(enc("alice email") @ enc("tectonic uplift basalt"))
    assert related > unrelated + 0.15, (related, unrelated)


def test_encoder_typo_tolerance():
    enc = encoder.get("hashing", dim=256)
    assert float(enc("hermione") @ enc("hermoine")) > 0.3


def test_encoder_is_unit_norm():
    enc = encoder.get("hashing", dim=128)
    assert abs(float(np.linalg.norm(enc("some words here"))) - 1.0) < 1e-5
    assert float(np.linalg.norm(enc(""))) == 0.0


def test_encoder_deterministic():
    a = encoder.get("hashing", dim=64)("repeatable")
    b = encoder.get("hashing", dim=64)("repeatable")
    assert np.array_equal(a, b)


# ------------------------------------------------------------------ bloom
def test_bloom_no_false_negatives():
    """The property the whole verification story depends on."""
    toks = [f"token{i}" for i in range(200)]
    filters = bloom.build(toks, 256, 6).reshape(-1, 1)   # [bytes, 1]
    for t in toks:
        assert bloom.contains_any(filters, [t], 6)[0]


def test_bloom_rejects_most_absent():
    toks = [f"present{i}" for i in range(200)]
    filters = bloom.build(toks, 256, 6).reshape(-1, 1)
    absent = [f"missing{i}" for i in range(500)]
    fp = sum(bool(bloom.contains_any(filters, [t], 6)[0]) for t in absent)
    assert fp / len(absent) < 0.10


def test_bloom_vectorised_matches_individual():
    cols = [bloom.build([f"a{i}", f"b{i}", "shared"], 128, 4) for i in range(20)]
    filters = np.stack(cols, axis=1)                      # [bytes, 20]
    assert bloom.contains_any(filters, ["a7"], 4)[7]
    assert bloom.contains_any(filters, ["shared"], 4).all()


def test_bloom_storage_is_byte_major(tmp_path):
    """Layout is load-bearing: row-major probing is ~17x slower at scale."""
    bag = _corpus(tmp_path)
    assert bag.cortex.blooms.shape == (bag.cfg.bloom_bytes, len(bag.cortex))
    assert bag.cortex.blooms.flags["C_CONTIGUOUS"]
    bag.close()


# ----------------------------------------------------------------- ledger
def test_ledger_dedupes_and_survives_reopen(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("hello bag")
    bag = Bag(tmp_path / "bag")
    assert bag.add(src)["ingested"] == 1
    assert bag.add(src).get("duplicate") == 1
    bag.close()

    reopened = Bag(tmp_path / "bag")
    assert len(list(reopened.ledger.entries())) == 1
    reopened.close()


def test_tombstone_is_logical(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("keep the bytes")
    bag = Bag(tmp_path / "bag")
    bag.add(src)
    entry = next(iter(bag.ledger.entries()))
    bag.ledger.tombstone(entry.entry_id, "user asked")
    assert len(list(bag.ledger.entries())) == 0
    assert len(list(bag.ledger.entries(include_deleted=True))) == 1
    bag.close()


# ----------------------------------------------------------- cortex/index
def _corpus(tmp_path) -> Bag:
    docs = {
        "sailing.md": "The boat project. Rigging the mainsail and the jib. "
                      "Keel repair scheduled for spring. Harbour fees paid.",
        "taxes.md": "Quarterly tax filing. Deductions for the home office. "
                    "Receipts from the accountant. VAT return submitted.",
        "physics.md": "Notes on fluid dynamics. Reynolds number and turbulent "
                      "flow around a hull. Boundary layer separation.",
        "recipes.md": "Sourdough starter needs feeding. Bake at 240C with "
                      "steam. Hydration around 75 percent.",
    }
    d = tmp_path / "docs"
    d.mkdir()
    for name, body in docs.items():
        (d / name).write_text(body)
    bag = Bag(tmp_path / "bag", Config(dim=256, chunk_chars=400))
    bag.add(d)
    return bag


def test_ingest_creates_one_intron_per_chunk(tmp_path):
    bag = _corpus(tmp_path)
    assert len(bag.cortex) == bag.clerk.stats()["chunks"] > 0
    bag.close()


def test_bloom_never_hides_a_clerk_hit(tmp_path):
    """The magic layer is additive: it must not bury a literal match."""
    bag = _corpus(tmp_path)
    for q in ["mainsail", "sourdough", "reynolds", "deductions"]:
        clerk_ids = {c for c, _ in bag.clerk.search(q, limit=20)}
        cortex_ids = {c for c, _ in bag.cortex.search(q, k=len(bag.cortex))}
        assert clerk_ids <= cortex_ids, q
    bag.close()


def test_weights_stay_unit_norm_under_hammering(tmp_path):
    """Guards the loud-intron failure mode: skewed traffic must not let one
    intron win on magnitude rather than fit."""
    bag = _corpus(tmp_path)
    target = bag.cortex.ids[0]
    for _ in range(300):
        bag.cortex.reinforce("boat rigging spring", [target])
    norms = np.linalg.norm(bag.cortex.w_fast, axis=1)
    assert norms.max() <= 1.0 + 1e-4
    bag.close()


def test_reinforce_moves_score_the_right_way(tmp_path):
    bag = _corpus(tmp_path)
    cid = bag.cortex.ids[0]
    i = bag.cortex.pos[cid]
    q = "a very specific unrelated phrase"
    before = float(bag.cortex.score_all(bag.cortex.encoder(q))[i])
    for _ in range(20):
        bag.cortex.reinforce(q, [cid])
    after = float(bag.cortex.score_all(bag.cortex.encoder(q))[i])
    assert after > before
    bag.close()


def test_consolidate_moves_fast_into_slow(tmp_path):
    bag = _corpus(tmp_path)
    cid = bag.cortex.ids[0]
    i = bag.cortex.pos[cid]
    slow_before = bag.cortex.w_slow[i].copy()
    for _ in range(10):
        bag.cortex.reinforce("keel repair harbour", [cid])
    bag.consolidate()
    assert not np.allclose(slow_before, bag.cortex.w_slow[i])
    assert abs(float(np.linalg.norm(bag.cortex.w_slow[i])) - 1.0) < 1e-4
    bag.close()


def test_exploration_reaches_everything(tmp_path):
    """Without epsilon-greedy, introns outside the initial top-K are
    permanently invisible."""
    bag = _corpus(tmp_path)
    n = len(bag.cortex)
    seen = set()
    for i in range(400):
        for cid, _ in bag.cortex.search(f"query number {i}", k=2,
                                        use_bloom=False):
            seen.add(cid)
    assert len(seen) > n * 0.9, (len(seen), n)
    bag.close()


# ------------------------------------------------------------------- race
def test_race_labels_itself(tmp_path):
    bag = _corpus(tmp_path)
    results, outcome = bag.find("mainsail rigging")
    assert results
    assert outcome.clerk_ids
    assert 0.0 <= outcome.recall_at_k <= 1.0
    assert bag.race.log_path.exists()
    bag.close()


def _codeword_corpus(tmp_path, n_topics=10, per_topic=5):
    """A corpus where the interesting queries are lexically invisible.

    Each topic's documents contain topic words. The user's private name for
    the topic -- the "codeword" -- appears in *no document at all*. Exact
    search can never find it. This is "the boat thing": the phrase you use
    for a project that the files themselves never say.
    """
    d = tmp_path / "docs"
    d.mkdir()
    topic_words = {t: [f"t{t}w{w}" for w in range(6)] for t in range(n_topics)}
    codeword = {t: f"codename{t}" for t in range(n_topics)}
    owner = {}
    for t in range(n_topics):
        for i in range(per_topic):
            name = f"doc_{t}_{i}.txt"
            (d / name).write_text(
                " ".join(topic_words[t]) + f" ordinary filler text {i}")
            owner[name] = t
    return d, topic_words, codeword, owner


def test_cortex_learns_associations_exact_search_cannot(tmp_path):
    """The decisive test, and the product's whole reason to exist.

    The user searches with a codeword that appears in no file. The Clerk can
    never answer it. But when the codeword is used *alongside* words the Clerk
    can match, the Clerk's answers teach the Cortex the association -- and
    afterwards the codeword works on its own.
    """
    bag = Bag(tmp_path / "bag",
              Config(dim=256, chunk_chars=400, epsilon=0.0, lr=0.12))
    d, topic_words, codeword, owner = _codeword_corpus(tmp_path)
    bag.add(d)
    topic_of = {cid: owner[bag.clerk.chunk_info(cid)["name"]]
                for cid in bag.cortex.ids}

    # The codeword is genuinely invisible to exact search.
    for t, cw in codeword.items():
        assert bag.clerk.search(cw) == [], cw

    def codeword_precision() -> float:
        total = 0.0
        for t, cw in codeword.items():
            hits = bag.cortex.search(cw, k=5, explore=False)
            total += sum(topic_of[c] == t for c, _ in hits) / 5
        return total / len(codeword)

    before = codeword_precision()

    # Realistic usage: codeword alongside something the Clerk can match.
    for _ in range(6):
        for t, cw in codeword.items():
            bag.find(f"{cw} {topic_words[t][0]} {topic_words[t][1]}", learn=True)
        bag.consolidate()

    after = codeword_precision()
    assert after > before + 0.15, (before, after)
    assert after > 0.5, after
    bag.close()


def test_learning_is_not_pure_memorisation(tmp_path):
    """Generalisation (T1): train on one codeword phrasing, test on another.

    If the encoder had no locality -- a hash of the whole query string, say --
    held-out phrasings would score no better than unrelated ones, and this
    would fail.
    """
    bag = Bag(tmp_path / "bag",
              Config(dim=256, chunk_chars=400, epsilon=0.0, lr=0.12))
    d, topic_words, codeword, owner = _codeword_corpus(tmp_path)
    bag.add(d)
    topic_of = {cid: owner[bag.clerk.chunk_info(cid)["name"]]
                for cid in bag.cortex.ids}

    def precision(make_query) -> float:
        total = 0.0
        for t, cw in codeword.items():
            hits = bag.cortex.search(make_query(t, cw), k=5, explore=False)
            total += sum(topic_of[c] == t for c, _ in hits) / 5
        return total / len(codeword)

    # Held-out phrasing: never trained on, and shares no whole-string identity
    # with the training phrasing.
    def heldout(t, cw):
        return f"the {cw} stuff"

    before = precision(heldout)

    for _ in range(6):
        for t, cw in codeword.items():
            bag.find(f"{cw} {topic_words[t][0]}", learn=True)
        bag.consolidate()

    after = precision(heldout)
    assert after > before + 0.10, (before, after)
    bag.close()


# ---------------------------------------------------------------- rebuild
def test_indexes_are_disposable(tmp_path):
    """Only the Ledger is precious. Prove it by destroying everything else."""
    bag = _corpus(tmp_path)
    before = {c for c, _ in bag.clerk.search("mainsail", limit=10)}
    n_introns = len(bag.cortex)
    bag.close()

    bag = Bag(tmp_path / "bag")
    counts = bag.rebuild()
    after = {c for c, _ in bag.clerk.search("mainsail", limit=10)}
    assert before == after
    assert counts["introns"] == n_introns
    assert len(bag.cortex) == n_introns
    bag.close()


def test_cortex_survives_roundtrip(tmp_path):
    bag = _corpus(tmp_path)
    cid = bag.cortex.ids[0]
    bag.cortex.reinforce("something memorable", [cid])
    w = bag.cortex.w_fast[bag.cortex.pos[cid]].copy()
    bag.close()

    bag = Bag(tmp_path / "bag")
    assert np.allclose(bag.cortex.w_fast[bag.cortex.pos[cid]], w)
    bag.close()


def test_dream_produces_towers(tmp_path):
    bag = _corpus(tmp_path)
    report = bag.dream(n_towers=3)
    assert report and sum(t["size"] for t in report) == len(bag.cortex)
    bag.close()


def test_extractors_cover_common_formats(tmp_path):
    from hermione import extract
    for suffix in [".txt", ".md", ".json", ".csv", ".html", ".pdf"]:
        assert suffix in extract.supported()


def test_mixed_formats_land_in_one_bag(tmp_path):
    d = tmp_path / "mixed"
    d.mkdir()
    (d / "a.md").write_text("# Boat\nRigging notes for the mainsail.")
    (d / "b.json").write_text('{"project": "boat", "task": "rigging"}')
    (d / "c.csv").write_text("project,task\nboat,rigging\n")
    (d / "d.html").write_text("<html><body><p>boat rigging</p></body></html>")
    bag = Bag(tmp_path / "bag")
    counts = bag.add(d)
    assert counts["ingested"] == 4
    results, _ = bag.find("boat rigging", learn=False)
    assert len({r.name for r in results}) >= 3
    bag.close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
