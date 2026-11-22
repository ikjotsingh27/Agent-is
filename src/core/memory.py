"""The agent's long-term memory: plain, readable Markdown files in data/memory/.

Every note is a normal .md file that the user can read, edit, or delete on the Memory screen (or in any text
editor). Nothing is hidden in a database, and notes are loaded only when the user or the agent asks for them,
so they do not bloat every model call.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .settings import data_dir

MAX_NOTE_CHARS = 50_000
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(title: str) -> str:
    raw = (title or "").strip().lower()
    if raw.endswith(".md"):
        raw = raw[:-3]
    slug = _SLUG_RE.sub("-", raw).strip("-")[:60]
    return slug or "note"


def _human_title(slug: str) -> str:
    words = slug.replace("-", " ").replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else "Note"


def _split_heading(raw_text: str, fallback_slug: str) -> tuple[str, str]:
    lines = (raw_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip() or _human_title(fallback_slug)
        body = "\n".join(lines[1:]).lstrip("\n").rstrip()
        return title, body
    return _human_title(fallback_slug), "\n".join(lines).strip()


@dataclass
class Note:
    slug: str
    title: str
    content: str
    path: Path
    updated: float = 0.0


class MemoryStore:
    def __init__(self, directory: Path | str | None = None):
        self.dir = Path(directory) if directory else data_dir() / "memory"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, title_or_slug: str) -> Path:
        return self.dir / f"{slugify(title_or_slug)}.md"

    def _read_path(self, path: Path) -> Note:
        raw = path.read_text(encoding="utf-8", errors="replace")
        title, body = _split_heading(raw, path.stem)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        return Note(slug=path.stem, title=title, content=body, path=path, updated=mtime)

    def list_notes(self) -> list[Note]:
        if not self.dir.exists():
            return []
        files = [
            p for p in self.dir.iterdir()
            if p.is_file() and p.suffix.lower() == ".md" and not p.name.startswith(".")
        ]
        notes = [self._read_path(p) for p in files]
        notes.sort(key=lambda n: (n.title.lower(), n.slug))
        return notes

    def get_note(self, title_or_slug: str) -> Note | None:
        slug = slugify(title_or_slug)
        path = self.dir / f"{slug}.md"
        if path.is_file():
            return self._read_path(path)
        low = (title_or_slug or "").strip().lower()
        for note in self.list_notes():
            if note.title.lower() == low or note.slug == low:
                return note
        return None

    def save_note(self, title: str, content: str, append: bool = False) -> Note:
        clean_title = " ".join((title or "").strip().split())[:80] or "Note"
        existing = self.get_note(clean_title)
        if existing is not None:
            path = existing.path
            if clean_title.lower() == existing.slug:
                clean_title = existing.title
        else:
            path = self._path_for(clean_title)
        body = (content or "").strip()
        if append and existing is not None and existing.content:
            body = f"{existing.content}\n\n{body}" if body else existing.content
        if len(body) > MAX_NOTE_CHARS:
            body = body[:MAX_NOTE_CHARS]
        full_text = f"# {clean_title}\n\n{body}\n" if body else f"# {clean_title}\n"
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(full_text, encoding="utf-8")
        os.replace(tmp, path)
        return self._read_path(path)

    def delete_note(self, title_or_slug: str) -> bool:
        note = self.get_note(title_or_slug)
        if note is None:
            return False
        try:
            note.path.unlink()
            return True
        except OSError:
            return False

    def search(self, query: str = "") -> list[Note]:
        notes = self.list_notes()
        q = (query or "").strip().lower()
        if not q:
            return notes
        words = [w for w in q.split() if w]
        matched = []
        for note in notes:
            hay = f"{note.title}\n{note.slug}\n{note.content}".lower()
            if q in hay or (words and all(w in hay for w in words)):
                matched.append(note)
        return matched
