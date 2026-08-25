"""The Bag: the runtime that owns a corpus and runs the metabolism over it.

GENERATION 1   ingest, digest, circulate, consolidate, dream, forget, rebuild
ITERATION 1a   + lineage: revisions detected on ingest, introns inherit
ITERATION 1b   + vaults: bytes may live elsewhere; the bag still knows
ITERATION 1c   + corpus journal: the bag remembers its own past

Six processes on different clocks:

    ingest       on drop            bytes -> Ledger. Instant, never blocks.
    digest       after ingest       extract, chunk -> Clerk, Cortex
    circulate    on query           race the layers, log the outcome
    consolidate  idle, minutes      fast weights -> slow weights
    dream        nightly            cluster into towers, surface connections
    forget       weekly             decay, compact, tier out

The file format is inert; the processes are the product. Genome vs cell.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from . import chunk as chunk_mod
from . import extract as extract_mod
from .clerk import Clerk, tokens
from .config import Config
from .corpus import CorpusJournal          # [1c]
from .cortex import Cortex
from .ledger import Ledger
from .race import Race

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
             ".hermione", ".DS_Store", "dist", "build", ".mypy_cache"}


class Bag:
    """A Hermione bag. Small opening, everything inside, nothing lost."""

    def __init__(self, root: Path, cfg: Optional[Config] = None):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.cfg_path = self.root / "config.json"
        self.cfg = cfg or Config.load(self.cfg_path)
        if not self.cfg_path.exists():
            self.cfg.save(self.cfg_path)

        # [1b] vaults are declared in config so they survive restarts
        self.ledger = Ledger(self.root / "ledger",
                             vaults=self.cfg.extra.get("vaults", {}))
        self.clerk = Clerk(self.root / "clerk.db")
        self.cortex = Cortex(self.root / "cortex", self.cfg)
        self.race = Race(self.clerk, self.cortex,
                         self.root / "querylog.jsonl")
        self.corpus = CorpusJournal(self.root / "corpus.jsonl")   # [1c]
        self.chunker = chunk_mod.get("paragraph")

    # ================= vaults (1b) ======================================
    def attach_vault(self, vault_id: str, path: Path) -> dict:
        """Tell the bag where a vault's bytes currently are.

        Vaults come and go -- that is the point. Attaching one makes its
        entries reachable again without changing a single Ledger record.
        """
        vaults = dict(self.cfg.extra.get("vaults", {}))
        vaults[vault_id] = str(Path(path).expanduser().resolve())
        self.cfg.extra["vaults"] = vaults
        self.cfg.save(self.cfg_path)
        self.ledger.vaults = vaults
        return vaults

    def detach_vault(self, vault_id: str) -> dict:
        vaults = dict(self.cfg.extra.get("vaults", {}))
        vaults.pop(vault_id, None)
        self.cfg.extra["vaults"] = vaults
        self.cfg.save(self.cfg_path)
        self.ledger.vaults = vaults
        return vaults

    def unreachable(self) -> List[dict]:
        """[1b] What we know about but cannot currently open."""
        out = []
        for e in self.ledger.entries():
            w = self.ledger.whereabouts(e)
            if not w["reachable"]:
                out.append({"name": e.name, "entry_id": e.entry_id,
                            "size": e.size, **w})
        return out

    # ================= ingest + digest ==================================
    def add(self, path: Path, *, recursive: bool = True,
            vault_id: str = "", progress=None) -> Dict[str, int]:   # [1b]
        """Drop anything in. Files, folders, whatever."""
        path = Path(path).expanduser()
        paths = self._walk(path) if path.is_dir() and recursive else [path]
        counts = Counter()
        new_ids: List[str] = []
        new_texts: List[str] = []
        inherit: Dict[str, str] = {}          # [1a] new chunk -> predecessor
        names: List[str] = []                 # [1c]
        # [1a] Index the current heads by filename once, not once per file.
        heads = self._head_index()

        for p in paths:
            counts["seen"] += 1
            try:
                parent = heads.get(Path(p).name)             # [1a]
                entry = self.ledger.add_file(
                    p, copy=self.cfg.copy_blobs,
                    max_bytes=self.cfg.max_blob_mb * 1024 * 1024,
                    parent_id=parent.entry_id if parent else "",
                    vault_id=vault_id)
            except (OSError, PermissionError):
                counts["unreadable"] += 1
                continue
            if entry is None:
                counts["duplicate"] += 1
                continue
            counts["ingested"] += 1
            names.append(entry.name)
            if parent:
                counts["revisions"] += 1                     # [1a]

            ids, texts = self._digest(entry)
            if ids:
                if parent:
                    inherit.update(self._inheritance(parent, ids, texts))
                new_ids += ids
                new_texts += texts
                counts["chunks"] += len(ids)
            else:
                counts["no_text"] += 1
            if progress:
                progress(p, counts)

        self.clerk.commit()
        if new_ids:
            counts["introns"] += self.cortex.add(new_ids, new_texts, inherit)
            counts["inherited"] += getattr(self.cortex, "inherited_last", 0)
            self.cortex.save()

        out = dict(counts)
        if out.get("ingested"):                              # [1c]
            self.corpus.record("ingest", names=names[:50], **out)
        return out

    def add_note(self, text: str, name: str = "note") -> Dict[str, int]:
        entry = self.ledger.add_note(text, name=name)
        if entry is None:
            return {"duplicate": 1}
        ids, texts = self._digest(entry)
        self.clerk.commit()
        n = self.cortex.add(ids, texts) if ids else 0
        self.cortex.save()
        out = {"ingested": 1, "chunks": len(ids), "introns": n}
        self.corpus.record("ingest", names=[name], **out)     # [1c]
        return out

    # -- iteration 1a: lineage detection ----------------------------------
    def _head_index(self) -> Dict[str, object]:
        """filename -> most recent head entry with that name.

        Cheap heuristic: same filename means probable revision. A personal
        dump is full of `report_v2_FINAL_actual.docx`, and the overwhelming
        majority of real revisions keep the name and change the bytes.
        Content confirmation happens per-chunk in `_inheritance`, so a false
        positive here costs nothing -- chunks that don't actually match
        simply don't inherit.

        Built once per ingest: doing it per file is quadratic, and a dump is
        exactly the case where that bites.
        """
        idx: Dict[str, object] = {}
        for e in self.ledger.heads():
            if e.kind != "file":
                continue
            prev = idx.get(e.name)
            if prev is None or e.ingested_at > prev.ingested_at:  # type: ignore
                idx[e.name] = e
        return idx

    def _inheritance(self, parent, new_ids: List[str],
                     new_texts: List[str]) -> Dict[str, str]:
        """Map new chunk ids to the predecessor chunks they replace."""
        old = self.clerk.entry_chunks(parent.entry_id)
        if not old:
            return {}
        matched = self.cortex.match_chunks(
            [c for c, _ in old], [t for _, t in old], new_texts)
        return {new_ids[i]: old_id for i, old_id in matched.items()
                if i < len(new_ids)}

    def _digest(self, entry) -> tuple[List[str], List[str]]:
        data = self.ledger.blob(entry)
        if data is None:
            return [], []
        text = extract_mod.extract(data, entry)
        if not text or not text.strip():
            return [], []
        # Give the indexes the filename too -- it is often the best signal a
        # dumped file has.
        text = f"{entry.name}\n\n{text}"
        chunks = self.chunker(text, self.cfg.chunk_chars, self.cfg.chunk_overlap)
        if not chunks:
            return [], []
        self.clerk.add_entry(entry)
        ids = self.clerk.add_chunks(entry.entry_id, chunks)
        return ids, [c.text for c in chunks]

    def _walk(self, root: Path) -> Iterable[Path]:
        for p in sorted(root.rglob("*")):
            if any(part in SKIP_DIRS or part.startswith(".")
                   for part in p.relative_to(root).parts):
                continue
            if p.is_file() and p.stat().st_size > 0:
                yield p

    # ================= circulate ========================================
    def find(self, query: str, limit: int = 10, learn: bool = True):
        return self.race.query(query, limit=limit, learn=learn)

    # ================= consolidate / dream / forget =====================
    def consolidate(self) -> dict:
        out = self.cortex.consolidate()
        self.cortex.save()
        self.corpus.record("consolidate", **out)              # [1c]
        return out

    def dream(self, n_towers: int = 8, top_terms: int = 6) -> List[dict]:
        """Cluster the slow weights. Towers precipitate; nobody declares them."""
        groups = self.cortex.towers(k=n_towers)
        report = []
        for cid, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            counter: Counter = Counter()
            files: Counter = Counter()
            for idx in members[:400]:
                chunk_id = self.cortex.ids[idx]
                info = self.clerk.chunk_info(chunk_id)
                if not info:
                    continue
                files[info.get("name") or "?"] += 1
                for t in tokens(info["text"]):
                    if len(t) > 3 and not t.isdigit():
                        counter[t] += 1
            for stop in _STOP:
                counter.pop(stop, None)
            report.append({
                "tower": cid,
                "size": len(members),
                "terms": [t for t, _ in counter.most_common(top_terms)],
                "files": [f for f, _ in files.most_common(4)],
            })
        (self.root / "towers.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False))
        self.corpus.record("dream", towers=len(report),            # [1c]
                           terms=[t["terms"][:3] for t in report[:6]])
        return report

    def forget(self) -> dict:
        """Weekly: let unused fast weights fade. Forgetting is a feature."""
        import numpy as np
        if len(self.cortex) == 0:
            return {"decayed": 0}
        before = float(np.linalg.norm(self.cortex.w_fast, axis=1).sum())
        self.cortex.w_fast *= self.cfg.fast_decay
        self.cortex.save()
        after = float(np.linalg.norm(self.cortex.w_fast, axis=1).sum())
        return {"decayed": len(self.cortex), "norm_before": before,
                "norm_after": after}

    # ================= rebuild ==========================================
    def rebuild(self, *, cortex_only: bool = False) -> dict:
        """Nothing is precious but the Ledger. Prove it."""
        if not cortex_only:
            self.clerk.close()
            (self.root / "clerk.db").unlink(missing_ok=True)
            self.clerk = Clerk(self.root / "clerk.db")

        for p in (self.root / "cortex").glob("*"):
            p.unlink()
        self.cortex = Cortex(self.root / "cortex", self.cfg)
        self.race = Race(self.clerk, self.cortex, self.root / "querylog.jsonl")

        counts = Counter()
        if cortex_only:
            ids, texts = [], []
            for cid, text in self.clerk.iter_chunks():
                ids.append(cid)
                texts.append(text)
            counts["introns"] = self.cortex.add(ids, texts)
        else:
            # [1a] Replay in ingest order so a revision is always rebuilt
            # after its predecessor, and inheritance can be reconstructed.
            entries = sorted(self.ledger.entries(),
                             key=lambda e: e.ingested_at)
            for entry in entries:
                ids, texts = self._digest(entry)
                if not ids:
                    continue
                inherit = {}
                if entry.parent_id:
                    parent = _Stub(entry.parent_id)
                    inherit = self._inheritance(parent, ids, texts)
                    counts["revisions"] += 1
                counts["entries"] += 1
                counts["chunks"] += len(ids)
                counts["introns"] += self.cortex.add(ids, texts, inherit)
                counts["inherited"] += getattr(
                    self.cortex, "inherited_last", 0)
            self.clerk.commit()
        self.cortex.save()
        return dict(counts)

    def relearn(self, passes: int = 1) -> List[dict]:
        """Replay the query log as training. The log is a free labelled set."""
        history = []
        for _ in range(passes):
            outcomes = self.race.replay()
            if not outcomes:
                break
            for o in outcomes:
                self.race._teach(o.query, o.clerk_ids, o.cortex_ids,
                                 o.cortex_only_verified)
            self.consolidate()
            history.append(self.evaluate())
        return history

    # ================= evaluation =======================================
    def evaluate(self, limit: Optional[int] = None) -> dict:
        """The one honest number, plus its supporting cast.

        `cortex_only` is the product's reason to exist: results the learned
        layer found that exact search could not.
        """
        outcomes = self.race.replay(limit=limit)
        if not outcomes:
            return {"queries": 0}
        n = len(outcomes)
        return {
            "queries": n,
            "recall_at_k": sum(o.recall_at_k for o in outcomes) / n,
            "cortex_only": sum(len(o.cortex_only_verified) for o in outcomes) / n,
            "clerk_empty": sum(1 for o in outcomes if not o.clerk_ids),
            "ms_clerk": sum(o.ms_clerk for o in outcomes) / n,
            "ms_cortex": sum(o.ms_cortex for o in outcomes) / n,
        }

    def status(self) -> dict:
        return {
            "root": str(self.root),
            "ledger": self.ledger.stats(),
            "clerk": self.clerk.stats(),
            "cortex": self.cortex.summary(),
            "unreachable": len(self.unreachable()),          # [1b]
            "corpus_events": len(self.corpus.records()),     # [1c]
            "queries_logged": sum(
                1 for _ in open(self.race.log_path, encoding="utf-8"))
            if self.race.log_path.exists() else 0,
        }

    def close(self) -> None:
        self.clerk.close()
        self.cortex.save()


class _Stub:
    """[1a] Minimal stand-in when only an entry_id is needed for lookup."""

    def __init__(self, entry_id: str):
        self.entry_id = entry_id


_STOP = {"this", "that", "with", "from", "have", "they", "there", "which",
         "what", "when", "your", "would", "about", "were", "been", "into",
         "will", "them", "their", "then", "than", "some", "more", "most",
         "such", "only", "just", "also", "here", "each", "other", "these",
         "http", "https", "www", "com", "html", "none", "true", "false"}
