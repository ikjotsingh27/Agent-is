"""Lazy skills loaded only when a user message matches a skill's triggers.

From docs/BLUEPRINT.md:
- "Lazy skills. No list of skills in the system prompt. A skill is loaded only when its trigger matches."
- Skills are optional plain Markdown files in data/skills/*.md with a simple header:
    triggers: resume, cv, cover letter
    ---
    Steps and guidance...
- When data/skills/ is empty or no trigger matches, zero tokens are added to the request.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .settings import data_dir

MAX_SKILL_CHARS = 2500
MAX_MATCHED_SKILLS = 2


@dataclass
class Skill:
    name: str
    triggers: list[str]
    body: str
    path: Path


def _parse_skill_file(path: Path) -> Skill | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not raw:
        return None
    triggers: list[str] = []
    body = raw
    lines = raw.splitlines()
    # Optional YAML-style --- frontmatter or leading "triggers: a, b" line
    if lines and lines[0].strip() == "---":
        end_idx = None
        for idx in range(1, min(len(lines), 25)):
            if lines[idx].strip() == "---":
                end_idx = idx
                break
            m = re.match(r"^triggers?\s*:\s*(.+)$", lines[idx].strip(), re.I)
            if m:
                triggers.extend(t.strip().strip("[]'\"").lower() for t in m.group(1).split(",") if t.strip())
        if end_idx is not None:
            body = "\n".join(lines[end_idx + 1:]).strip()
    elif lines:
        m = re.match(r"^triggers?\s*:\s*(.+)$", lines[0].strip(), re.I)
        if m:
            triggers.extend(t.strip().strip("[]'\"").lower() for t in m.group(1).split(",") if t.strip())
            body = "\n".join(lines[1:]).strip()
            if body.startswith("---"):
                body = body[3:].strip()
    triggers = [t for t in triggers if len(t) >= 2]
    if not triggers or not body:
        return None
    return Skill(name=path.stem.replace("-", " ").replace("_", " ").strip(),
                 triggers=triggers, body=body[:MAX_SKILL_CHARS], path=path)


class SkillStore:
    def __init__(self, skills_dir: Path | str | None = None):
        self.dir = Path(skills_dir) if skills_dir else data_dir() / "skills"

    def list_skills(self) -> list[Skill]:
        if not self.dir.is_dir():
            return []
        found = []
        for path in sorted(self.dir.glob("*.md")):
            sk = _parse_skill_file(path)
            if sk is not None:
                found.append(sk)
        return found

    def match(self, user_text: str) -> list[Skill]:
        low = (user_text or "").lower()
        if not low:
            return []
        matched = []
        for sk in self.list_skills():
            for trig in sk.triggers:
                pattern = r"\b" + re.escape(trig) + r"\b" if trig.isalnum() else re.escape(trig)
                if re.search(pattern, low):
                    matched.append(sk)
                    break
            if len(matched) >= MAX_MATCHED_SKILLS:
                break
        return matched

    def format_matched(self, user_text: str) -> str:
        skills = self.match(user_text)
        if not skills:
            return ""
        parts = []
        for sk in skills:
            parts.append(f"[Matched skill: {sk.name}]\n{sk.body}")
        return "\n\n".join(parts)
