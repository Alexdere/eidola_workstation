"""The Clerk: deterministic, exact, boring, works on day one.

SQLite FTS5. No cleverness anywhere in this file, on purpose. The Clerk has two
jobs:

  1. Make the bag useful before the Cortex knows anything.
  2. Grade the Cortex's homework, forever, for free.

Job 2 is the reason the Cortex can learn without the user ever labelling
anything. Every query the Clerk can answer is a supervised example.

Fully rebuildable from the Ledger.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .chunk import Chunk

_WORD = re.compile(r"\w+", re.UNICODE)

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    entry_id    TEXT PRIMARY KEY,
    name        TEXT,
    suffix      TEXT,
    source_path TEXT,
    size        INTEGER,
    ingested_at REAL
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id    TEXT PRIMARY KEY,
    entry_id    TEXT NOT NULL,
    ord         INTEGER NOT NULL,
    char_start  INTEGER,
    char_end    INTEGER,
    text        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_entry ON chunks(entry_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, chunk_id UNINDEXED, tokenize='unicode61'
);
"""


def tokens(text: str) -> List[str]:
    return [t.lower() for t in _WORD.findall(text)]


class Clerk:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self._tune()
        try:
            self.db.executescript(SCHEMA)
        except sqlite3.OperationalError as exc:
            if "disk i/o" not in str(exc).lower():
                raise
            # Cloud-synced and network filesystems (iCloud Drive, Dropbox,
            # SMB) often do not implement the POSIX locking SQLite wants.
            # Fall back to the most conservative settings before giving up
            # with an explanation the user can act on.
            self.db.close()
            self.db = sqlite3.connect(str(self.path))
            self.db.row_factory = sqlite3.Row
            self._tune(conservative=True)
            try:
                self.db.executescript(SCHEMA)
            except sqlite3.OperationalError:
                raise RuntimeError(
                    f"SQLite cannot operate on {self.path}.\n"
                    "This usually means the bag is on a cloud-synced or "
                    "network drive (iCloud Drive, Dropbox, a network share), "
                    "which does not support the file locking SQLite needs.\n"
                    "Put the bag on local disk instead -- e.g. "
                    "`--bag ~/.hermione` -- and point it at the synced folder "
                    "with `hermione add`. The bag is an index; the originals "
                    "can live anywhere."
                ) from exc
        self.db.commit()

    def _tune(self, conservative: bool = False) -> None:
        pragmas = ["PRAGMA synchronous=NORMAL", "PRAGMA temp_store=MEMORY"]
        pragmas.append("PRAGMA journal_mode=DELETE" if conservative
                       else "PRAGMA journal_mode=WAL")
        if conservative:
            pragmas.append("PRAGMA locking_mode=EXCLUSIVE")
        for p in pragmas:
            try:
                self.db.execute(p)
            except sqlite3.OperationalError:
                pass

    # -- writing ----------------------------------------------------------
    def add_entry(self, entry) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?,?)",
            (entry.entry_id, entry.name, entry.suffix, entry.source_path,
             entry.size, entry.ingested_at))

    def add_chunks(self, entry_id: str, chunks: Iterable[Chunk]) -> List[str]:
        ids = []
        for c in chunks:
            chunk_id = f"{entry_id}:{c.ord}"
            self.db.execute(
                "INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?,?)",
                (chunk_id, entry_id, c.ord, c.char_start, c.char_end, c.text))
            self.db.execute(
                "INSERT INTO chunks_fts (text, chunk_id) VALUES (?,?)",
                (c.text, chunk_id))
            ids.append(chunk_id)
        return ids

    def commit(self) -> None:
        self.db.commit()

    # -- reading ----------------------------------------------------------
    @staticmethod
    def _fts_query(q: str) -> Optional[str]:
        """Build a safe FTS5 expression. User text is never interpolated raw."""
        terms = [t for t in tokens(q) if len(t) > 1]
        if not terms:
            return None
        # Quoted terms are literals to FTS5; OR keeps recall high, and the
        # bm25 ranking sorts out which matches actually matter.
        return " OR ".join(f'"{t}"' for t in terms)

    def search(self, query: str, limit: int = 20) -> List[Tuple[str, float]]:
        expr = self._fts_query(query)
        if expr is None:
            return []
        try:
            rows = self.db.execute(
                "SELECT chunk_id, bm25(chunks_fts) AS score FROM chunks_fts "
                "WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?",
                (expr, limit)).fetchall()
        except sqlite3.OperationalError:
            return []
        # bm25 returns negative values, lower is better -> flip to positive.
        return [(r["chunk_id"], -float(r["score"])) for r in rows]

    def chunk_text(self, chunk_id: str) -> Optional[str]:
        row = self.db.execute(
            "SELECT text FROM chunks WHERE chunk_id=?", (chunk_id,)).fetchone()
        return row["text"] if row else None

    def chunk_info(self, chunk_id: str) -> Optional[dict]:
        row = self.db.execute(
            "SELECT c.chunk_id, c.entry_id, c.ord, c.text, e.name, e.suffix, "
            "e.source_path FROM chunks c LEFT JOIN entries e "
            "ON e.entry_id = c.entry_id WHERE c.chunk_id=?",
            (chunk_id,)).fetchone()
        return dict(row) if row else None

    def all_chunk_ids(self) -> List[str]:
        return [r[0] for r in self.db.execute(
            "SELECT chunk_id FROM chunks ORDER BY entry_id, ord")]

    def iter_chunks(self):
        for r in self.db.execute(
                "SELECT chunk_id, text FROM chunks ORDER BY entry_id, ord"):
            yield r["chunk_id"], r["text"]

    def entry_chunks(self, entry_id: str) -> List[Tuple[str, str]]:
        """[1a] Chunks of one entry, in order. Used for lineage matching."""
        return [(r["chunk_id"], r["text"]) for r in self.db.execute(
            "SELECT chunk_id, text FROM chunks WHERE entry_id=? ORDER BY ord",
            (entry_id,))]

    def stats(self) -> dict:
        n_e = self.db.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        n_c = self.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return {"entries": n_e, "chunks": n_c,
                "bytes": self.path.stat().st_size if self.path.exists() else 0}

    def close(self) -> None:
        self.db.commit()
        self.db.close()
