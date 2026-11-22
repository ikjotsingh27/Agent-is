"""The always-on background loop, scheduled tasks, quiet runs, and Start-with-Windows helper.

Rules from the blueprint:
- Quiet runs: a scheduled check that finds nothing new replies 'NOTHING_NEW', sends no message, and makes no
  extra model calls.
- Ignored check-ins: the user chooses in Settings whether Pilot keeps going quietly ("keep_going", default) or
  pauses scheduled check-ins ("pause") after 6 unanswered check-ins in a row.
- Pause All stops the background loop immediately.
- Start with Windows places a small launcher in the user's Windows Startup folder (no administrator rights
  needed) so Pilot recovers after a reboot.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .settings import project_root

QUIET_TOKEN = "NOTHING_NEW"
QUIET_INSTRUCTION = (
    "\n[Scheduled background check: if there is nothing new or important to report, reply with only NOTHING_NEW.]"
)
MAX_IGNORED_BEFORE_PAUSE = 6


@dataclass
class ScheduledJob:
    id: str
    name: str
    prompt: str
    interval_minutes: int
    enabled: bool = True
    last_run: float = 0.0


def default_startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return Path.home() / ".config" / "autostart"


def startup_launcher_path(startup_dir: Path | str | None = None) -> Path:
    folder = Path(startup_dir) if startup_dir else default_startup_dir()
    return folder / "Pilot.cmd"


def is_start_with_windows_enabled(startup_dir: Path | str | None = None) -> bool:
    return startup_launcher_path(startup_dir).is_file()


def set_start_with_windows(enabled: bool, startup_dir: Path | str | None = None,
                           root_dir: Path | str | None = None) -> Path:
    """Create or remove Pilot.cmd in the user's Windows Startup folder."""
    launcher = startup_launcher_path(startup_dir)
    if not enabled:
        try:
            launcher.unlink(missing_ok=True)
        except OSError:
            pass
        return launcher
    root = Path(root_dir) if root_dir else project_root()
    py_exe = Path(sys.executable)
    pythonw = py_exe.with_name("pythonw.exe")
    exe = pythonw if pythonw.is_file() else py_exe
    script = (
        "@echo off\r\n"
        f'cd /d "{root}"\r\n'
        f'start "" "{exe}" -m src daemon\r\n'
    )
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(script, encoding="utf-8")
    return launcher


class Scheduler:
    """Runs recurring background tasks while Pilot is active and not paused."""

    def __init__(self, settings, run_job, on_report=None, save_settings=None, clock=None, sleep=None):
        self.settings = settings
        self.run_job = run_job  # fn(job: ScheduledJob) -> reply_text
        self.on_report = on_report or (lambda job, reply, quiet: None)
        self.save_settings = save_settings or (lambda: None)
        self._clock = clock or time.time
        self._stop = threading.Event()
        self._sleep = sleep or self._stop.wait
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

    def jobs(self) -> list[ScheduledJob]:
        raw = self.settings.get("schedules", default=[]) or []
        out = []
        for idx, item in enumerate(raw, 1):
            if not isinstance(item, dict) or not item.get("prompt"):
                continue
            out.append(
                ScheduledJob(
                    id=str(item.get("id") or f"job_{idx}"),
                    name=str(item.get("name") or f"Task {idx}"),
                    prompt=str(item.get("prompt")),
                    interval_minutes=max(1, int(item.get("interval_minutes", 60) or 60)),
                    enabled=bool(item.get("enabled", True)),
                    last_run=float(item.get("last_run", 0.0) or 0.0),
                )
            )
        return out

    def _save_jobs(self, jobs: list[ScheduledJob]) -> None:
        raw = [
            {
                "id": j.id,
                "name": j.name,
                "prompt": j.prompt,
                "interval_minutes": j.interval_minutes,
                "enabled": j.enabled,
                "last_run": j.last_run,
            }
            for j in jobs
        ]
        self.settings.set("schedules", raw)
        self.save_settings()

    def add_job(self, name: str, prompt: str, interval_minutes: int = 60) -> ScheduledJob:
        with self._lock:
            current = self.jobs()
            used_ids = {j.id for j in current}
            n = len(current) + 1
            while f"job_{n}" in used_ids:
                n += 1
            job = ScheduledJob(
                id=f"job_{n}",
                name=(name or prompt[:40] or f"Task {n}").strip(),
                prompt=prompt.strip(),
                interval_minutes=max(1, int(interval_minutes or 60)),
                enabled=True,
                last_run=self._clock(),
            )
            current.append(job)
            self._save_jobs(current)
            return job

    def remove_job(self, job_id_or_name: str) -> bool:
        target = (job_id_or_name or "").strip().lower()
        with self._lock:
            current = self.jobs()
            kept = [j for j in current if j.id.lower() != target and j.name.lower() != target]
            if len(kept) == len(current):
                return False
            self._save_jobs(kept)
            return True

    def set_job_enabled(self, job_id: str, enabled: bool) -> bool:
        with self._lock:
            current = self.jobs()
            found = False
            for j in current:
                if j.id == job_id:
                    j.enabled = bool(enabled)
                    found = True
            if found:
                self._save_jobs(current)
            return found

    def note_user_activity(self) -> None:
        """Reset the unanswered check-in counter whenever the user sends a message."""
        if self.settings.get("schedule_policy", "unanswered_count", default=0):
            self.settings.set("schedule_policy", "unanswered_count", 0)
            self.save_settings()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="scheduler")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                pass
            self._sleep(10.0)

    def tick(self) -> list[tuple[ScheduledJob, str, bool]]:
        """Run any due scheduled jobs once. Returns list of (job, reply_text, is_quiet)."""
        if self.settings.get("runtime", "paused", default=False):
            return []
        policy = self.settings.get("schedule_policy", default={}) or {}
        when_ignored = policy.get("when_ignored", "keep_going")
        unanswered = int(policy.get("unanswered_count", 0) or 0)
        if when_ignored == "pause" and unanswered >= MAX_IGNORED_BEFORE_PAUSE:
            return []

        now = self._clock()
        jobs = self.jobs()
        ran = []
        changed = False
        for job in jobs:
            if not job.enabled:
                continue
            due_at = job.last_run + job.interval_minutes * 60.0
            if now < due_at:
                continue
            job.last_run = now
            changed = True
            try:
                reply_text = (self.run_job(job) or "").strip()
            except Exception as exc:
                reply_text = f"Scheduled task '{job.name}' failed: {exc}"
            cleaned = reply_text.strip(" .!\"'")
            is_quiet = not cleaned or cleaned.upper() == QUIET_TOKEN
            if not is_quiet:
                unanswered += 1
                self.settings.set("schedule_policy", "unanswered_count", unanswered)
            try:
                self.on_report(job, "" if is_quiet else reply_text, is_quiet)
            except Exception:
                pass
            ran.append((job, "" if is_quiet else reply_text, is_quiet))
        if changed:
            with self._lock:
                self._save_jobs(jobs)
        return ran
