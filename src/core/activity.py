"""Plain, searchable activity log and daily plain-language summary.

Every action Pilot takes (chat tasks, terminal commands, file/PDF/memory tools, connector messages,
scheduled runs, approvals, and Pause All) is written to data/usage/activity.jsonl and summarized in
data/usage/YYYY-MM-DD-summary.txt. Secrets are always redacted before writing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .settings import data_dir

LONG_RUN_SECONDS = 45.0


def _now() -> datetime:
    return datetime.now().astimezone()


@dataclass
class ActivityEntry:
    time: str
    start_time: str
    end_time: str
    seconds: float
    source: str  # "window", "web", "telegram", "discord", "schedule", "system"
    kind: str  # "chat", "command", "tool", "approval", "schedule", "pause", "error"
    summary: str
    tokens: int = 0
    long_run: bool = False
    quiet: bool = False


class ActivityLog:
    def __init__(self, settings, usage_dir: Path | str | None = None, now=None):
        self.settings = settings
        self.dir = Path(usage_dir) if usage_dir else data_dir() / "usage"
        self._now = now or _now

    def log_path(self) -> Path:
        return self.dir / "activity.jsonl"

    def summary_path(self, day=None) -> Path:
        day = day or self._now().date()
        return self.dir / f"{day.isoformat()}-summary.txt"

    def record(
        self,
        *,
        summary: str,
        kind: str = "chat",
        source: str = "window",
        started: float | None = None,
        ended: float | None = None,
        tokens: int = 0,
        quiet: bool = False,
    ) -> ActivityEntry:
        now_dt = self._now()
        if started is not None and ended is not None:
            duration = round(max(0.0, ended - started), 2)
            start_dt = datetime.fromtimestamp(started, tz=now_dt.tzinfo) if started > 1_000_000 else now_dt
            end_dt = datetime.fromtimestamp(ended, tz=now_dt.tzinfo) if ended > 1_000_000 else now_dt
            start_str = start_dt.strftime("%H:%M")
            end_str = end_dt.strftime("%H:%M")
        else:
            duration = 0.0
            start_str = end_str = now_dt.strftime("%H:%M")
        clean_summary = self.settings.redact(" ".join((summary or "").split()))[:240]
        long_cap = float((self.settings.get("limits", default={}) or {}).get("long_run_seconds", LONG_RUN_SECONDS))
        is_long = duration >= long_cap
        entry = ActivityEntry(
            time=now_dt.isoformat(timespec="seconds"),
            start_time=start_str,
            end_time=end_str,
            seconds=duration,
            source=source,
            kind=kind,
            summary=clean_summary,
            tokens=int(tokens or 0),
            long_run=is_long,
            quiet=bool(quiet),
        )
        self.dir.mkdir(parents=True, exist_ok=True)
        row = {
            "time": entry.time,
            "start_time": entry.start_time,
            "end_time": entry.end_time,
            "seconds": entry.seconds,
            "source": entry.source,
            "kind": entry.kind,
            "summary": entry.summary,
            "tokens": entry.tokens,
            "long_run": entry.long_run,
            "quiet": entry.quiet,
        }
        with open(self.log_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        if not entry.quiet and self.settings.get("usage", "daily_summary", default=True):
            self._append_daily_summary(entry, now_dt.date())
        return entry

    def format_line(self, entry: ActivityEntry) -> str:
        time_part = (
            f"{entry.start_time} to {entry.end_time}"
            if entry.start_time != entry.end_time
            else entry.start_time
        )
        tok_part = f", {entry.tokens:,} tokens" if entry.tokens else ""
        dur_part = f" ({entry.seconds:.1f}s — ran long)" if entry.long_run else ""
        quiet_part = " (quiet run)" if entry.quiet else ""
        body = entry.summary.rstrip(".")
        return f"{time_part}, [{entry.source}] {body}{tok_part}{dur_part}{quiet_part}."

    def _append_daily_summary(self, entry: ActivityEntry, day) -> None:
        try:
            with open(self.summary_path(day), "a", encoding="utf-8") as fh:
                fh.write(self.format_line(entry) + "\n")
        except OSError:
            pass

    def entries(self, query: str = "", limit: int = 200) -> list[ActivityEntry]:
        path = self.log_path()
        if not path.is_file():
            return []
        q = (query or "").strip().lower()
        words = [w for w in q.split() if w]
        out: list[ActivityEntry] = []
        for raw in reversed(path.read_text(encoding="utf-8", errors="replace").splitlines()):
            if not raw.strip():
                continue
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            entry = ActivityEntry(
                time=str(d.get("time", "")),
                start_time=str(d.get("start_time", "")),
                end_time=str(d.get("end_time", "")),
                seconds=float(d.get("seconds", 0.0) or 0.0),
                source=str(d.get("source", "window")),
                kind=str(d.get("kind", "chat")),
                summary=str(d.get("summary", "")),
                tokens=int(d.get("tokens", 0) or 0),
                long_run=bool(d.get("long_run", False)),
                quiet=bool(d.get("quiet", False)),
            )
            if q:
                hay = f"{entry.time} {entry.start_time} {entry.source} {entry.kind} {entry.summary}".lower()
                if q not in hay and not (words and all(w in hay for w in words)):
                    continue
            out.append(entry)
            if len(out) >= limit:
                break
        return out

    def daily_summary_lines(self, day=None) -> list[str]:
        path = self.summary_path(day)
        if path.is_file():
            return [ln for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        target_prefix = (day or self._now().date()).isoformat()
        return [
            self.format_line(e)
            for e in reversed(self.entries(limit=500))
            if e.time.startswith(target_prefix)
        ]

    def export_text(self, dest: Path | str, query: str = "") -> Path:
        out_path = Path(dest)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["Pilot Activity Log", "==================", ""]
        summary_lines = self.daily_summary_lines()
        if summary_lines:
            lines.append("Today's Summary:")
            lines.extend(f"  - {s}" for s in summary_lines)
            lines.append("")
        lines.append("Timeline:")
        for e in self.entries(query=query, limit=1000):
            lines.append("  " + self.format_line(e))
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return out_path
