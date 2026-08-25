"""The Ledger: append-only, content-addressed, and the only precious thing.

GENERATION 1   core journal + blob store
ITERATION 1a   + parent_id           edit lineage
ITERATION 1b   + presence, vault_id  bytes may live elsewhere, or nowhere

Contract
--------
    The Ledger is truth.
    The Clerk is derived from the Ledger.
    The Cortex is derived from the Ledger and the query log.
    Delete either index at any time. Nothing is lost but time.

Nothing in this module may ever mutate or remove an existing record. Deletion
is logical: a tombstone entry is appended. That is what makes it safe to
experiment destructively with everything downstream.

[1b] The journal and the bytes are now separate concerns. The journal is
light, always present, and portable -- a few hundred bytes per entry. The
bytes live in a *vault*, which may be an external drive, may be unplugged,
may be gone. `presence` records which. This is what lets you search a drive
that isn't connected: the Ledger still knows what was on it.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional

# [1b] Where the bytes for an entry currently are.
PRESENCE_HERE = "here"        # blob is inside this bag
PRESENCE_VAULTED = "vaulted"  # blob is in a named vault (maybe unplugged)
PRESENCE_LINKED = "linked"    # not copied; only the original path is known
PRESENCE_MISSING = "missing"  # we knew it once and can no longer find it


@dataclass
class Entry:
    entry_id: str            # content address (sha256 of bytes), 16 hex chars
    sha256: str
    source_path: str
    name: str
    suffix: str
    size: int
    ingested_at: float
    kind: str = "file"       # file | note | tombstone
    meta: dict = field(default_factory=dict)

    # --- iteration 1a ----------------------------------------------------
    parent_id: str = ""
    """The entry this one is an edit of, if any.

    Content addressing means an edited file gets a brand new entry_id with no
    relationship to its predecessor. Without this field the Cortex discards
    everything it learned about a document the moment you edit it. With it,
    introns inherit rather than reinitialise -- learning survives editing."""

    # --- iteration 1b ----------------------------------------------------
    presence: str = PRESENCE_HERE
    vault_id: str = ""
    """Which vault holds the bytes. Empty means this bag holds them."""


class Ledger:
    """Append-only journal + content-addressed blob store."""

    def __init__(self, root: Path, vaults: Optional[Dict[str, str]] = None):
        self.root = Path(root)
        self.journal_path = self.root / "journal.jsonl"
        self.blobs = self.root / "blobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.journal_path.touch(exist_ok=True)
        # [1b] vault_id -> filesystem path. Any of these may be absent.
        self.vaults: Dict[str, str] = dict(vaults or {})

    # -- writing ----------------------------------------------------------
    def _append(self, record: dict) -> None:
        with open(self.journal_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        if hasattr(self, "_seen") and record.get("kind") != "tombstone":
            self._seen.add(record["entry_id"])

    def add_file(self, path: Path, *, copy: bool = True,
                 max_bytes: Optional[int] = None,
                 meta: Optional[dict] = None,
                 parent_id: str = "",                     # [1a]
                 vault_id: str = "") -> Optional[Entry]:   # [1b]
        """Ingest a file. Returns None if it is a byte-identical duplicate."""
        path = Path(path)
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        entry_id = digest[:16]

        if self.has(entry_id):
            return None

        # [1b] presence follows from what we actually did with the bytes.
        presence = PRESENCE_LINKED
        if copy and (max_bytes is None or len(data) <= max_bytes):
            dest = self._blob_path(digest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                tmp = dest.with_suffix(".tmp")
                tmp.write_bytes(data)
                tmp.replace(dest)
            presence = PRESENCE_HERE
        elif vault_id:
            presence = PRESENCE_VAULTED

        entry = Entry(
            entry_id=entry_id,
            sha256=digest,
            source_path=str(path.resolve()),
            name=path.name,
            suffix=path.suffix.lower(),
            size=len(data),
            ingested_at=time.time(),
            kind="file",
            meta=meta or {},
            parent_id=parent_id,
            presence=presence,
            vault_id=vault_id,
        )
        self._append(asdict(entry))
        return entry

    def add_note(self, text: str, *, name: str = "note",
                 meta: Optional[dict] = None,
                 parent_id: str = "") -> Optional[Entry]:   # [1a]
        data = text.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        entry_id = digest[:16]
        if self.has(entry_id):
            return None
        dest = self._blob_path(digest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        entry = Entry(entry_id, digest, "", name, ".txt", len(data),
                      time.time(), "note", meta or {}, parent_id=parent_id)
        self._append(asdict(entry))
        return entry

    def tombstone(self, entry_id: str, reason: str = "") -> None:
        """Logical delete. The bytes stay. Only the intent is recorded."""
        self._append({"entry_id": entry_id, "kind": "tombstone",
                      "ingested_at": time.time(), "meta": {"reason": reason}})

    # -- reading ----------------------------------------------------------
    def _blob_path(self, digest: str) -> Path:
        return self.blobs / digest[:2] / digest

    def blob(self, entry: Entry) -> Optional[bytes]:
        """[1b] Resolve bytes: local blob -> vault -> original path -> None."""
        p = self._blob_path(entry.sha256)
        if p.exists():
            return p.read_bytes()

        if entry.vault_id and entry.vault_id in self.vaults:
            vp = (Path(self.vaults[entry.vault_id])
                  / entry.sha256[:2] / entry.sha256)
            if vp.exists():
                return vp.read_bytes()

        src = Path(entry.source_path)
        if src.exists():
            return src.read_bytes()
        return None

    def whereabouts(self, entry: Entry) -> dict:
        """[1b] Where are these bytes, and can we get them right now?

        This is the answer to "search a drive that isn't plugged in": the
        Ledger always knows the file existed and what it was about, and can
        tell you which drive to go and find.
        """
        if self._blob_path(entry.sha256).exists():
            return {"reachable": True, "where": "bag"}
        if entry.vault_id:
            if entry.vault_id in self.vaults:
                vp = (Path(self.vaults[entry.vault_id])
                      / entry.sha256[:2] / entry.sha256)
                if vp.exists():
                    return {"reachable": True,
                            "where": f"vault:{entry.vault_id}"}
            return {"reachable": False, "where": f"vault:{entry.vault_id}",
                    "hint": f"attach vault {entry.vault_id!r}"}
        if Path(entry.source_path).exists():
            return {"reachable": True, "where": "original path"}
        return {"reachable": False, "where": "unknown",
                "hint": "bytes not copied and original path is gone"}

    def records(self) -> Iterator[dict]:
        with open(self.journal_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def entries(self, include_deleted: bool = False) -> Iterator[Entry]:
        """Replay the journal into live entries."""
        live: dict[str, dict] = {}
        dead: set[str] = set()
        for rec in self.records():
            if rec.get("kind") == "tombstone":
                dead.add(rec["entry_id"])
            else:
                live[rec["entry_id"]] = rec
        known = set(Entry.__dataclass_fields__)
        for eid, rec in live.items():
            if eid in dead and not include_deleted:
                continue
            yield Entry(**{k: v for k, v in rec.items() if k in known})

    # -- iteration 1a: lineage --------------------------------------------
    def children(self) -> Dict[str, List[str]]:
        """parent_id -> [entry_id, ...]"""
        out: Dict[str, List[str]] = {}
        for e in self.entries():
            if e.parent_id:
                out.setdefault(e.parent_id, []).append(e.entry_id)
        return out

    def lineage(self, entry_id: str) -> List[Entry]:
        """Oldest-first chain of versions ending at `entry_id`."""
        by_id = {e.entry_id: e for e in self.entries(include_deleted=True)}
        chain: List[Entry] = []
        seen = set()
        cur = by_id.get(entry_id)
        while cur and cur.entry_id not in seen:
            seen.add(cur.entry_id)
            chain.append(cur)
            cur = by_id.get(cur.parent_id) if cur.parent_id else None
        return list(reversed(chain))

    def heads(self) -> List[Entry]:
        """Entries nothing else claims as a parent -- the current versions."""
        kids = self.children()
        return [e for e in self.entries() if e.entry_id not in kids]

    def has(self, entry_id: str) -> bool:
        """Pure membership test, memoised against the journal."""
        if not hasattr(self, "_seen"):
            self._seen = {r["entry_id"] for r in self.records()
                          if r.get("kind") != "tombstone"}
        return entry_id in self._seen

    def stats(self) -> dict:
        n = size = 0
        presence: Dict[str, int] = {}
        revisions = 0
        for e in self.entries():
            n += 1
            size += e.size
            presence[e.presence] = presence.get(e.presence, 0) + 1  # [1b]
            if e.parent_id:                                          # [1a]
                revisions += 1
        return {"entries": n, "bytes": size,
                "journal_bytes": self.journal_path.stat().st_size,
                "presence": presence, "revisions": revisions,
                "vaults_attached": sorted(self.vaults)}
