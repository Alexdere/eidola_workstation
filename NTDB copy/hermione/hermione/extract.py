"""Digest: bytes -> text. A registry, so new formats are one function.

Register a handler for an extension and it is picked up everywhere:

    @extractor(".epub")
    def _epub(data: bytes, entry) -> str:
        ...
"""
from __future__ import annotations

import csv
import io
import json
import re
from typing import Callable, Dict, Optional

Handler = Callable[[bytes, object], Optional[str]]
_REGISTRY: Dict[str, Handler] = {}


def extractor(*suffixes: str):
    def deco(fn: Handler) -> Handler:
        for s in suffixes:
            _REGISTRY[s.lower()] = fn
        return fn
    return deco


def supported() -> list[str]:
    return sorted(_REGISTRY)


def extract(data: bytes, entry) -> Optional[str]:
    """Return plain text for an entry, or None if it cannot be read."""
    fn = _REGISTRY.get(getattr(entry, "suffix", "").lower())
    if fn is not None:
        try:
            return fn(data, entry)
        except Exception:
            return _fallback(data)
    return _fallback(data)


def _fallback(data: bytes) -> Optional[str]:
    """Last resort: if it decodes as text and looks like text, keep it."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("latin-1")
        except Exception:
            return None
    if not text.strip():
        return None
    printable = sum(c.isprintable() or c.isspace() for c in text[:2000])
    if printable / max(1, len(text[:2000])) < 0.85:
        return None
    return text


# --- handlers ------------------------------------------------------------

@extractor(".txt", ".md", ".markdown", ".rst", ".log", ".py", ".js", ".ts",
           ".java", ".c", ".h", ".cpp", ".go", ".rs", ".sh", ".yaml", ".yml",
           ".toml", ".ini", ".cfg", ".sql", ".org", ".tex")
def _plain(data: bytes, entry) -> Optional[str]:
    return _fallback(data)


@extractor(".json", ".jsonl", ".ndjson")
def _json(data: bytes, entry) -> Optional[str]:
    text = _fallback(data)
    if text is None:
        return None
    out: list[str] = []

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(node, list):
            for i, v in enumerate(node[:500]):
                walk(v, f"{path}[{i}]")
        else:
            out.append(f"{path}: {node}")

    try:
        walk(json.loads(text))
    except json.JSONDecodeError:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                walk(json.loads(line))
            except json.JSONDecodeError:
                out.append(line)
    return "\n".join(out)


@extractor(".csv", ".tsv")
def _csv(data: bytes, entry) -> Optional[str]:
    text = _fallback(data)
    if text is None:
        return None
    delim = "\t" if entry.suffix == ".tsv" else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    if not rows:
        return None
    header = rows[0]
    lines = [" | ".join(header)]
    for row in rows[1:]:
        lines.append(" | ".join(
            f"{h}={v}" for h, v in zip(header, row) if v))
    return "\n".join(lines)


@extractor(".html", ".htm", ".xhtml")
def _html(data: bytes, entry) -> Optional[str]:
    text = _fallback(data)
    if text is None:
        return None
    try:
        from bs4 import BeautifulSoup  # optional
        soup = BeautifulSoup(text, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n"))
    except ImportError:
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        return re.sub(r"[ \t]{2,}", " ", text)


@extractor(".pdf")
def _pdf(data: bytes, entry) -> Optional[str]:
    try:
        from pypdf import PdfReader  # optional
    except ImportError:
        return None
    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    text = "\n\n".join(parts).strip()
    return text or None


@extractor(".docx")
def _docx(data: bytes, entry) -> Optional[str]:
    try:
        import docx  # optional
    except ImportError:
        return None
    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for table in d.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts) or None
