"""Saved chat sessions on disk (data/chats/<id>.json).

Every conversation is saved automatically so clicking 'New chat' starts a fresh session without losing earlier
ones, and closing/reopening Pilot restores your active session plus the full sidebar list of past sessions.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .settings import data_dir


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _auto_title(text: str) -> str:
    clean = " ".join((text or "").split())
    if clean.startswith("[Files the user sent you"):
        parts = text.split("\n\n", 1)
        clean = " ".join((parts[1] if len(parts) > 1 else "").split())
    if not clean:
        return "New chat"
    return clean if len(clean) <= 42 else clean[:39].rstrip() + "..."


@dataclass
class SessionMeta:
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int = 0


class SessionStore:
    def __init__(self, directory: Path | str | None = None):
        self.dir = Path(directory) if directory else data_dir() / "chats"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        safe = "".join(ch for ch in (session_id or "") if ch.isalnum() or ch in ("-", "_"))
        if not safe:
            raise ValueError("Invalid session id.")
        return self.dir / f"{safe}.json"

    def _active_file(self) -> Path:
        return self.dir / "_active.txt"

    def active_id(self) -> str:
        p = self._active_file()
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8").strip()
            except OSError:
                return ""
        return ""

    def set_active_id(self, session_id: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / "._active.tmp"
        tmp.write_text(session_id, encoding="utf-8")
        os.replace(tmp, self._active_file())

    def create_session(self, title: str = "New chat") -> dict:
        now = _now_iso()
        sid = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        session = {
            "id": sid,
            "title": title or "New chat",
            "created_at": now,
            "updated_at": now,
            "turns": [],
            "summary_lines": [],
            "display": [],
        }
        self.save_session(session)
        self.set_active_id(sid)
        return session

    def load_session(self, session_id: str) -> dict | None:
        try:
            path = self._path(session_id)
        except ValueError:
            return None
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not data.get("id"):
            return None
        data.setdefault("title", "New chat")
        data.setdefault("turns", [])
        data.setdefault("summary_lines", [])
        data.setdefault("display", [])
        data.setdefault("created_at", _now_iso())
        data.setdefault("updated_at", data["created_at"])
        return data

    def save_session(self, session: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._path(session["id"])
        session["updated_at"] = _now_iso()
        if session.get("title") in ("", "New chat"):
            for item in session.get("display") or []:
                if item.get("who") in ("you", "you_phone") and item.get("text"):
                    session["title"] = _auto_title(item["text"])
                    break
            else:
                for turn in session.get("turns") or []:
                    if turn and isinstance(turn[0].get("content"), str):
                        session["title"] = _auto_title(turn[0]["content"])
                        break
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(session, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def rename_session(self, session_id: str, new_title: str) -> bool:
        data = self.load_session(session_id)
        if not data:
            return False
        clean = " ".join((new_title or "").split())[:60]
        if not clean:
            return False
        data["title"] = clean
        self.save_session(data)
        return True

    def delete_session(self, session_id: str) -> bool:
        try:
            path = self._path(session_id)
        except ValueError:
            return False
        if not path.is_file():
            return False
        try:
            path.unlink()
        except OSError:
            return False
        if self.active_id() == session_id:
            remaining = self.list_sessions()
            self.set_active_id(remaining[0].id if remaining else "")
        return True

    def list_sessions(self) -> list[SessionMeta]:
        if not self.dir.exists():
            return []
        out: list[SessionMeta] = []
        for path in self.dir.iterdir():
            if not path.is_file() or path.suffix.lower() != ".json" or path.name.startswith("."):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or not data.get("id"):
                continue
            display = data.get("display") or []
            turns = data.get("turns") or []
            out.append(SessionMeta(
                id=str(data["id"]),
                title=str(data.get("title") or "New chat"),
                created_at=str(data.get("created_at") or ""),
                updated_at=str(data.get("updated_at") or ""),
                message_count=len(display) if display else len(turns) * 2,
            ))
        out.sort(key=lambda s: (s.updated_at, s.created_at), reverse=True)
        return out

    def migrate_legacy_history(self, legacy_path: Path) -> dict | None:
        """Import a single legacy .chat_history.json into a saved session once if no sessions exist yet."""
        if self.list_sessions() or not legacy_path.is_file():
            return None
        try:
            data = json.loads(legacy_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        turns = data.get("turns") if isinstance(data, dict) else None
        if not isinstance(turns, list) or not turns:
            return None
        session = self.create_session()
        session["turns"] = [t for t in turns if isinstance(t, list) and t]
        session["summary_lines"] = [str(s) for s in (data.get("summary_lines") or []) if s]
        display = []
        for turn in session["turns"]:
            first = turn[0]
            if first.get("role") == "user" and isinstance(first.get("content"), str):
                u_txt = first["content"]
                if u_txt.startswith("[Files the user sent you"):
                    parts = u_txt.split("\n\n", 1)
                    u_txt = parts[1] if len(parts) > 1 else u_txt
                display.append({"who": "you", "text": u_txt, "extra": "", "steps": [], "files": []})
            reply = ""
            for msg in turn[1:]:
                if msg.get("role") == "assistant":
                    c = msg.get("content")
                    if isinstance(c, str) and c:
                        reply = c
                    elif isinstance(c, list):
                        t = "".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
                        if t and t != "(cut off by output limit)":
                            reply = t
            if reply:
                display.append({"who": "pilot", "text": reply, "extra": "", "steps": [], "files": []})
        session["display"] = display
        self.save_session(session)
        return session
