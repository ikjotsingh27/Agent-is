"""Keeps every model call small.

Only the last few turns are sent in full. A turn is one user message plus everything that follows
it until the next user message, so a tool call and its result are never split. Older turns are
squeezed into a few short summary lines, which ride along inside the first message that is sent.
The summary here is built by simple trimming (free). A model-written summary can replace it later.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def _first_text(message: dict, limit: int = 120) -> str:
    content = message.get("content")
    if isinstance(content, str):
        text = content
    else:
        text = " ".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def summarize_turn(turn: list) -> str:
    user = _first_text(turn[0])
    reply = ""
    for message in turn[1:]:
        if message["role"] == "assistant":
            reply = _first_text(message) or reply
    line = f"- asked: {user}"
    if reply:
        line += f" | answered: {reply}"
    return line


class Conversation:
    def __init__(self, recent_turns: int = 6, max_summary_chars: int = 1200, storage_path: Path | str | None = None):
        self.recent_turns = max(1, int(recent_turns))
        self.max_summary_chars = int(max_summary_chars)
        self.storage_path = Path(storage_path) if storage_path else None
        self.turns: list = []
        self.summary_lines: list = []
        if self.storage_path:
            self.load()

    def load(self) -> None:
        if not self.storage_path or not self.storage_path.is_file():
            return
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                turns = data.get("turns")
                summaries = data.get("summary_lines")
                if isinstance(turns, list):
                    self.turns = [t for t in turns if isinstance(t, list) and t]
                if isinstance(summaries, list):
                    self.summary_lines = [str(s) for s in summaries if s]
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        if not self.storage_path:
            return
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.storage_path.with_name(self.storage_path.name + ".tmp")
            payload = {"turns": self.turns, "summary_lines": self.summary_lines}
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, self.storage_path)
        except OSError:
            pass

    def add_user(self, text: str) -> int:
        """Start a new turn and return its index (used to roll it back if the task fails)."""
        self.turns.append([{"role": "user", "content": text}])
        while len(self.turns) > self.recent_turns:
            self.summary_lines.append(summarize_turn(self.turns.pop(0)))
        self._trim_summary()
        return len(self.turns) - 1

    def add_message(self, message: dict) -> None:
        self.turns[-1].append(message)

    def rollback(self, index: int) -> None:
        del self.turns[index:]

    def reset(self) -> None:
        self.turns.clear()
        self.summary_lines.clear()
        if self.storage_path:
            try:
                self.storage_path.unlink(missing_ok=True)
            except OSError:
                pass

    def summary(self) -> str:
        return "\n".join(self.summary_lines)

    def _trim_summary(self) -> None:
        while self.summary_lines and len("\n".join(self.summary_lines)) > self.max_summary_chars:
            if len(self.summary_lines) == 1:
                self.summary_lines[0] = self.summary_lines[0][: self.max_summary_chars]
                break
            self.summary_lines.pop(0)

    def window(self) -> list:
        messages = [dict(m) for turn in self.turns for m in turn]
        summary = self.summary()
        if summary and messages and isinstance(messages[0]["content"], str):
            messages[0]["content"] = f"[Earlier in this conversation:\n{summary}]\n\n{messages[0]['content']}"
        return messages
