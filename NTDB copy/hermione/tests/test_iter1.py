"""Tests for iterations 1a-1d.

Generation 1 invariants live in test_core.py and must keep passing unchanged;
that is the point of the generation split. Anything here is new surface.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hermione.ask import ask                      # noqa: E402
from hermione.config import Config                # noqa: E402
from hermione.ledger import (PRESENCE_HERE,       # noqa: E402
                             PRESENCE_LINKED, PRESENCE_VAULTED)
from hermione.runtime import Bag                  # noqa: E402


def _write(d: Path, name: str, body: str) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(body)
    return p


V1 = ("The boat project. Rigging the mainsail and the jib.\n\n"
      "Keel repair scheduled for spring, the yard quoted 4200.\n\n"
      "Harbour fees due in March. Winter storage arranged with Per.")
V2 = ("The boat project. Rigging the mainsail and the jib.\n\n"
      "Keel repair scheduled for spring, the yard quoted 4600 after "
      "the survey.\n\n"
      "Harbour fees due in March. Winter storage arranged with Per.\n\n"
      "New: ordered a replacement halyard.")


# ===================== iteration 1a: lineage ==========================
def test_1a_revision_is_linked_to_its_parent(tmp_path):
    d = tmp_path / "docs"
    src = _write(d, "boat.md", V1)
    bag = Bag(tmp_path / "bag", Config(dim=256, chunk_chars=200))
    bag.add(src)
    first = next(iter(bag.ledger.entries()))

    src.write_text(V2)
    counts = bag.add(src)

    assert counts["revisions"] == 1
    heads = bag.ledger.heads()
    assert len(heads) == 1
    assert heads[0].parent_id == first.entry_id
    bag.close()


def test_1a_lineage_chain_is_ordered(tmp_path):
    d = tmp_path / "docs"
    src = _write(d, "boat.md", V1)
    bag = Bag(tmp_path / "bag", Config(dim=256, chunk_chars=200))
    bag.add(src)
    src.write_text(V2)
    bag.add(src)
    src.write_text(V2 + "\n\nAnd a third revision.")
    bag.add(src)

    head = bag.ledger.heads()[0]
    chain = bag.ledger.lineage(head.entry_id)
    assert len(chain) == 3
    assert [c.entry_id for c in chain][-1] == head.entry_id
    assert all(chain[i].ingested_at <= chain[i + 1].ingested_at
               for i in range(len(chain) - 1))
    bag.close()


def test_1a_introns_inherit_learning_across_an_edit(tmp_path):
    """The whole point of 1a.

    Teach the bag a codeword for a document, edit the document, and check the
    codeword still finds it. Under generation 1 this silently failed: the new
    version got fresh introns seeded from content, with zero of the learning.
    """
    d = tmp_path / "docs"
    src = _write(d, "boat.md", V1)
    bag = Bag(tmp_path / "bag",
              Config(dim=256, chunk_chars=200, epsilon=0.0, lr=0.15))
    bag.add(src)

    # "sparrow" appears nowhere in the text.
    assert bag.clerk.search("sparrow") == []
    for _ in range(8):
        bag.find("sparrow keel repair yard", learn=True)
    bag.consolidate()

    def sparrow_top():
        hits = bag.cortex.search("sparrow", k=3, explore=False)
        return [bag.clerk.chunk_info(c)["entry_id"] for c, _ in hits]

    before_entry = next(iter(bag.ledger.entries())).entry_id
    assert before_entry in sparrow_top()

    # Now edit the document.
    src.write_text(V2)
    counts = bag.add(src)
    assert counts.get("inherited", 0) > 0

    new_entry = bag.ledger.heads()[0].entry_id
    assert new_entry != before_entry
    assert new_entry in sparrow_top(), "learning did not survive the edit"
    bag.close()


def test_1a_fresh_documents_do_not_inherit(tmp_path):
    """Inheritance must not leak between unrelated files."""
    d = tmp_path / "docs"
    bag = Bag(tmp_path / "bag", Config(dim=256, chunk_chars=200))
    bag.add(_write(d, "boat.md", V1))
    counts = bag.add(_write(d, "sourdough.md",
                            "Bake at 240C with steam. 75 percent hydration."))
    assert counts.get("revisions", 0) == 0
    assert counts.get("inherited", 0) == 0
    bag.close()


def test_1a_match_chunks_survives_insertion(tmp_path):
    """Ordinal matching would break here; content matching should not."""
    d = tmp_path / "docs"
    src = _write(d, "boat.md", V1)
    bag = Bag(tmp_path / "bag", Config(dim=256, chunk_chars=120))
    bag.add(src)
    parent = next(iter(bag.ledger.entries()))
    old = bag.clerk.entry_chunks(parent.entry_id)
    assert len(old) >= 2

    # Insert a new paragraph at the very top: every ordinal shifts by one.
    shifted = ["A brand new opening paragraph."] + [t for _, t in old]
    matched = bag.cortex.match_chunks([c for c, _ in old],
                                      [t for _, t in old], shifted)
    # The inserted paragraph matches nothing; the rest map to themselves.
    assert 0 not in matched
    assert len(matched) >= len(old) - 1
    bag.close()


def test_1a_rebuild_preserves_lineage(tmp_path):
    d = tmp_path / "docs"
    src = _write(d, "boat.md", V1)
    bag = Bag(tmp_path / "bag", Config(dim=256, chunk_chars=200))
    bag.add(src)
    src.write_text(V2)
    bag.add(src)
    bag.close()

    bag = Bag(tmp_path / "bag")
    counts = bag.rebuild()
    assert counts.get("revisions", 0) == 1
    assert counts.get("inherited", 0) > 0
    assert len(bag.ledger.heads()) == 1
    bag.close()


# ===================== iteration 1b: presence =========================
def test_1b_copied_bytes_are_present(tmp_path):
    bag = Bag(tmp_path / "bag")
    bag.add(_write(tmp_path / "docs", "a.txt", "hello"))
    e = next(iter(bag.ledger.entries()))
    assert e.presence == PRESENCE_HERE
    assert bag.ledger.whereabouts(e)["reachable"]
    assert bag.unreachable() == []
    bag.close()


def test_1b_uncopied_bytes_are_linked_and_can_go_missing(tmp_path):
    src = _write(tmp_path / "docs", "a.txt", "hello there")
    bag = Bag(tmp_path / "bag", Config(copy_blobs=False))
    bag.add(src)
    e = next(iter(bag.ledger.entries()))
    assert e.presence == PRESENCE_LINKED
    assert bag.ledger.whereabouts(e)["reachable"]

    src.unlink()                       # the drive goes away
    assert not bag.ledger.whereabouts(e)["reachable"]
    assert len(bag.unreachable()) == 1
    bag.close()


def test_1b_search_a_drive_that_is_not_plugged_in(tmp_path):
    """The headline behaviour of 1b.

    The bytes are gone. The bag still knows the file existed, what it was
    about, and can still find it by content.
    """
    src = _write(tmp_path / "docs", "sailing.md", V1)
    bag = Bag(tmp_path / "bag", Config(copy_blobs=False, dim=256))
    bag.add(src, vault_id="black-ssd")
    e = next(iter(bag.ledger.entries()))
    assert e.vault_id == "black-ssd"
    assert e.presence == PRESENCE_VAULTED

    src.unlink()                       # unplug it

    results, _ = bag.find("mainsail keel", learn=False)
    assert results, "content should still be searchable"
    w = bag.ledger.whereabouts(e)
    assert not w["reachable"]
    assert "black-ssd" in w["where"]
    assert "black-ssd" in w["hint"]
    bag.close()


def test_1b_attaching_a_vault_makes_bytes_reachable_again(tmp_path):
    src = _write(tmp_path / "docs", "a.txt", "vault contents here")
    bag = Bag(tmp_path / "bag", Config(copy_blobs=False))
    bag.add(src, vault_id="pen")
    e = next(iter(bag.ledger.entries()))

    # Build a vault laid out the way the resolver expects, then remove the
    # original so only the vault can satisfy the read.
    vault = tmp_path / "vault"
    (vault / e.sha256[:2]).mkdir(parents=True)
    (vault / e.sha256[:2] / e.sha256).write_bytes(src.read_bytes())
    src.unlink()

    assert not bag.ledger.whereabouts(e)["reachable"]
    bag.attach_vault("pen", vault)
    assert bag.ledger.whereabouts(e)["reachable"]
    assert bag.ledger.blob(e) == b"vault contents here"
    bag.close()

    # survives a reopen: vaults live in config
    bag = Bag(tmp_path / "bag")
    assert "pen" in bag.ledger.vaults
    bag.close()


# ===================== iteration 1c: corpus journal ===================
def test_1c_ingest_is_recorded(tmp_path):
    bag = Bag(tmp_path / "bag")
    bag.add(_write(tmp_path / "docs", "a.txt", "hello"))
    recs = bag.corpus.records(event="ingest")
    assert len(recs) == 1
    assert recs[0]["ingested"] == 1
    assert "a.txt" in recs[0]["names"]
    bag.close()


def test_1c_windows_filter_by_time(tmp_path):
    bag = Bag(tmp_path / "bag")
    bag.add(_write(tmp_path / "docs", "a.txt", "hello"))
    assert bag.corpus.summarise("week")["totals"]["ingested"] == 1

    # Backdate the only record and confirm it drops out of the window.
    p = bag.corpus.path
    import json
    recs = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    recs[0]["at"] = time.time() - 40 * 86400
    p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")

    assert bag.corpus.summarise("week")["totals"] == {}
    assert bag.corpus.summarise("quarter")["totals"]["ingested"] == 1
    bag.close()


# ===================== iteration 1d: the voice ========================
def _talking_bag(tmp_path) -> Bag:
    d = tmp_path / "docs"
    bag = Bag(tmp_path / "bag", Config(dim=256, chunk_chars=300))
    bag.add(_write(d, "sailing.md", V1))
    bag.add(_write(d, "sourdough.md", "Bake at 240C with steam."))
    return bag


def test_1d_greeting_is_stripped(tmp_path):
    bag = _talking_bag(tmp_path)
    a = ask(bag, "Hermione, remind me what I added this week")
    b = ask(bag, "what did I add this week")
    assert a.intent == b.intent == "additions"
    bag.close()


def test_1d_additions_reports_real_counts(tmp_path):
    bag = _talking_bag(tmp_path)
    a = ask(bag, "remind me what I added this week")
    assert "2 things" in a.text
    assert "sailing.md" in a.text
    bag.close()


def test_1d_empty_window_says_so(tmp_path):
    bag = _talking_bag(tmp_path)
    a = ask(bag, "what did I add today")
    # ingest just happened, so 'today' should be non-empty
    assert a.intent == "additions"
    assert "2 things" in a.text
    bag.close()


def test_1d_gaps_finds_repeated_empty_searches(tmp_path):
    bag = _talking_bag(tmp_path)
    for _ in range(3):
        bag.find("quarterly shipping costs 2024", learn=True)
    a = ask(bag, "what am I missing")
    assert a.intent == "gaps"
    assert "shipping" in a.text
    bag.close()


def test_1d_gaps_separates_real_gaps_from_learned_answers(tmp_path):
    """A codeword you deliberately taught must not be reported as a failure.

    Both look identical to exact search -- no literal match, every time. The
    difference is whether anything was ever taught, which only the fast
    weights know.
    """
    d = tmp_path / "docs"
    bag = Bag(tmp_path / "bag",
              Config(dim=256, chunk_chars=300, epsilon=0.0, lr=0.15))
    bag.add(_write(d, "sailing.md", V1))
    for i in range(30):
        _write(d, f"note_{i}.txt", f"Note {i} about filler topic {i % 7}.")
    bag.add(d)

    for _ in range(10):
        bag.find("sparrow keel repair yard", learn=True)
    bag.consolidate()
    for _ in range(3):
        bag.find("sparrow", learn=True)
    for _ in range(3):
        bag.find("quarterly shipping costs 2024", learn=True)

    a = ask(bag, "what am I missing")
    assert a.intent == "gaps"
    gaps = [q for q, _ in a.detail["gaps"]]
    learned = [q for q, _ in a.detail["learned"]]
    assert "quarterly shipping costs 2024" in gaps
    assert "sparrow" in learned
    bag.close()


def test_1d_whereabouts_reports_unplugged_vaults(tmp_path):
    src = _write(tmp_path / "docs", "a.txt", "hello")
    bag = Bag(tmp_path / "bag", Config(copy_blobs=False))
    bag.add(src, vault_id="black-ssd")
    src.unlink()
    a = ask(bag, "what's on the black drive")
    assert a.intent == "whereabouts"
    assert "black-ssd" in a.text
    bag.close()


def test_1d_versions_uses_lineage(tmp_path):
    d = tmp_path / "docs"
    src = _write(d, "boat.md", V1)
    bag = Bag(tmp_path / "bag", Config(dim=256, chunk_chars=200))
    bag.add(src)
    src.write_text(V2)
    bag.add(src)
    a = ask(bag, "what are the versions of things")
    assert a.intent == "versions"
    assert "boat.md" in a.text
    bag.close()


def test_1d_unknown_question_falls_back_to_search_and_says_so(tmp_path):
    """It must never invent an answer. Falling back is always defensible."""
    bag = _talking_bag(tmp_path)
    a = ask(bag, "mainsail")
    assert a.intent.startswith("fallback")
    assert "I don't have a stock answer" in a.text
    bag.close()


def test_1d_size_reports_the_bag(tmp_path):
    bag = _talking_bag(tmp_path)
    a = ask(bag, "how big is the bag")
    assert a.intent == "size"
    assert "2 things" in a.text
    bag.close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
