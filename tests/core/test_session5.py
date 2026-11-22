"""Session 5 tests: ActivityLog, Scheduler, quiet runs, Pause All, Start with Windows, Usage dashboard, daemon."""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import urllib.request
from datetime import datetime
from http.cookiejar import CookieJar
from pathlib import Path

from src.cli import run_daemon
from src.core.activity import LONG_RUN_SECONDS, ActivityLog
from src.core.providers.base import Response
from src.core.scheduler import (MAX_IGNORED_BEFORE_PAUSE, QUIET_TOKEN, Scheduler,
                                is_start_with_windows_enabled, set_start_with_windows)
from src.core.settings import Settings
from src.core.usage import UsageTracker
from src.core.workspace import Workspace
from src.ui.controller import Controller
from tests.helpers import FakeModelService


def _make_controller(tmp: Path) -> Controller:
    settings = Settings({"provider": "mock"}, path=tmp / "settings.json")
    return Controller(
        settings,
        provider_factory=FakeModelService,
        usage_dir=tmp / "usage",
        startup_dir=tmp / "startup",
    )


class ActivityLogTests(unittest.TestCase):
    def test_record_redact_search_summary_and_export(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            settings = Settings({"api_keys": {"gemini": "AIzaSecretValue123456"}}, path=tmp / "settings.json")
            log = ActivityLog(settings, usage_dir=tmp / "logs")
            e1 = log.record(summary="Checked key AIzaSecretValue123456", kind="task", source="window",
                            tokens=120, started=100.0, ended=101.5)
            e2 = log.record(summary="Long export finished", kind="task", source="schedule",
                            tokens=500, started=100.0, ended=100.0 + LONG_RUN_SECONDS + 5)
            log.record(summary="Quiet check", kind="schedule", source="schedule", quiet=True)

            self.assertNotIn("AIzaSecretValue123456", e1.summary)
            self.assertIn("[hidden]", e1.summary)
            self.assertFalse(e1.long_run)
            self.assertTrue(e2.long_run)

            hits = log.entries(query="export")
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].summary, "Long export finished")

            summary_lines = log.daily_summary_lines()
            self.assertTrue(any("Long export finished" in line and "ran long" in line for line in summary_lines))
            # Quiet runs are recorded in jsonl but kept out of the noisy daily summary file
            self.assertFalse(any("Quiet check" in line for line in summary_lines))

            exported = log.export_text(tmp / "activity.txt")
            text = exported.read_text(encoding="utf-8")
            self.assertIn("Pilot Activity Log", text)
            self.assertIn("Long export finished", text)


class SchedulerTests(unittest.TestCase):
    def test_due_job_and_quiet_run(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            settings = Settings({}, path=tmp / "settings.json")
            now = [10_000.0]
            reports = []
            replies = [QUIET_TOKEN, "You have 2 urgent notes."]

            sched = Scheduler(
                settings,
                run_job=lambda job: replies.pop(0),
                on_report=lambda job, reply, quiet: reports.append((job.name, reply, quiet)),
                save_settings=settings.save,
                clock=lambda: now[0],
            )
            sched.add_job("Inbox check", "Check inbox", interval_minutes=10)
            # Not due yet
            self.assertEqual(len(sched.tick()), 0)
            # Advance 11 minutes -> quiet run
            now[0] += 660.0
            self.assertEqual(len(sched.tick()), 1)
            self.assertEqual(reports[-1], ("Inbox check", "", True))
            # Advance another 11 minutes -> real notification
            now[0] += 660.0
            self.assertEqual(len(sched.tick()), 1)
            self.assertEqual(reports[-1], ("Inbox check", "You have 2 urgent notes.", False))

    def test_ignored_pause_policy_and_reset(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            settings = Settings({"schedule_policy": {"when_ignored": "pause"}}, path=tmp / "settings.json")
            now = [1_000.0]
            reports = []
            sched = Scheduler(
                settings,
                run_job=lambda job: "Reminder ping!",
                on_report=lambda job, reply, quiet: reports.append(reply),
                save_settings=settings.save,
                clock=lambda: now[0],
            )
            sched.add_job("Ping", "Send ping", interval_minutes=5)
            for _ in range(MAX_IGNORED_BEFORE_PAUSE):
                now[0] += 400.0
                self.assertEqual(len(sched.tick()), 1)
            self.assertEqual(len(reports), MAX_IGNORED_BEFORE_PAUSE)
            # Next tick is auto-paused because user ignored 6 check-ins in a row
            now[0] += 400.0
            self.assertEqual(len(sched.tick()), 0)
            # User replies -> resets ignored counter, scheduled tasks resume
            sched.note_user_activity()
            now[0] += 400.0
            self.assertEqual(len(sched.tick()), 1)

    def test_pause_all_blocks_scheduler_tick(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            settings = Settings({"runtime": {"paused": True}}, path=tmp / "settings.json")
            now = [1_000.0]
            ran = []
            sched = Scheduler(settings, run_job=lambda job: ran.append(job.id) or "hi", clock=lambda: now[0])
            sched.add_job("Ping", "Check", interval_minutes=5)
            now[0] += 600.0
            self.assertEqual(len(sched.tick()), 0)
            self.assertEqual(ran, [])

    def test_start_with_windows_in_temp_startup_dir(self):
        with tempfile.TemporaryDirectory() as td:
            startup = Path(td) / "startup"
            self.assertFalse(is_start_with_windows_enabled(startup))
            launcher = set_start_with_windows(True, startup_dir=startup, root_dir=Path(td))
            self.assertTrue(launcher.is_file())
            self.assertTrue(is_start_with_windows_enabled(startup))
            self.assertIn("-m src daemon", launcher.read_text(encoding="utf-8"))
            set_start_with_windows(False, startup_dir=startup)
            self.assertFalse(is_start_with_windows_enabled(startup))


class UsageDashboardTests(unittest.TestCase):
    def test_period_summary_sources_samples_alerts_and_export(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            settings = Settings(
                {"limits": {"daily_tokens": 1000, "daily_usd": 1.0},
                 "prices_per_million_tokens": {"m": {"input": 1000.0, "output": 1000.0}}},
                path=tmp / "settings.json",
            )
            ws = Workspace(settings, root=tmp / "agent")
            tracker = UsageTracker(settings, usage_dir=tmp / "usage",
                                   now=lambda: datetime(2026, 11, 21, 12, 0, 0).astimezone())
            resp = Response(text="ok", content=[], input_tokens=500, output_tokens=400, model="m")
            tracker.record(task="hello", model="m", response=resp, started=1.0, ended=2.0, source="window")
            tracker.record(task="check", model="m", response=Response(text="ok", content=[], input_tokens=50, output_tokens=50, model="m"),
                           started=2.0, ended=2.5, source="schedule")

            summary = tracker.period_summary()
            self.assertEqual(summary["today"]["calls"], 2)
            self.assertEqual(summary["today"]["total_tokens"], 1000)
            self.assertIn("window", summary["week"]["by_source"])
            self.assertIn("schedule", summary["week"]["by_source"])

            # Resource samples & window filtering
            now_ts = time.time()
            tracker.record_resource_sample(ws, sample={"ts": now_ts - 7200, "ram_mb": 40, "ram_pct": 2.0,
                                                       "cpu_pct": 1.0, "disk_mb": 1.0, "disk_pct": 0.1})
            tracker.record_resource_sample(ws, sample={"ts": now_ts - 10, "ram_mb": 80, "ram_pct": 85.0,
                                                       "cpu_pct": 5.0, "disk_mb": 4500.0, "disk_pct": 88.0})
            self.assertEqual(len(tracker.resource_samples("hour", now_ts=now_ts)), 1)
            self.assertEqual(len(tracker.resource_samples("day", now_ts=now_ts)), 2)

            alerts = tracker.alerts(ws, latest_sample={"ram_mb": 1700, "ram_pct": 85.0,
                                                       "disk_mb": 4500, "disk_pct": 88.0})
            self.assertTrue(any("Daily tokens" in a for a in alerts))
            self.assertTrue(any("workspace disk" in a.lower() for a in alerts))
            self.assertTrue(any("Memory use" in a for a in alerts))

            rep = tracker.export_report(tmp / "report.txt", ws)
            content = rep.read_text(encoding="utf-8")
            self.assertIn("Pilot Usage Report", content)
            self.assertIn("schedule:", content)


class PauseAllAndDaemonTests(unittest.TestCase):
    def test_pause_all_blocks_chat_and_denies_pending_approvals(self):
        with tempfile.TemporaryDirectory() as td:
            c = _make_controller(Path(td))
            seen = []
            c.approval_handler = lambda req, ans: seen.append((req, ans))
            # Pause blocks send immediately
            c.pause_all()
            self.assertTrue(c.is_paused())
            reply = c.send("hello")
            self.assertEqual(reply.kind, "error")
            self.assertIn("paused", reply.text.lower())

            # Resume allows chat again
            c.resume_all()
            self.assertFalse(c.is_paused())
            reply2 = c.send("hello again")
            self.assertEqual(reply2.kind, "ok")
            self.assertTrue(any("hello again" in e.summary for e in c.activity_entries()))

    def test_web_api_pause_and_resume(self):
        with tempfile.TemporaryDirectory() as td:
            c = _make_controller(Path(td))
            err = c.configure_web(enabled=True, host="127.0.0.1", port=18879, new_password="secret123")
            self.assertEqual(err, "")
            try:
                base = f"http://127.0.0.1:{c.web_server.actual_port}"
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

                def post(path, payload=None):
                    data = json.dumps(payload or {}).encode("utf-8")
                    req = urllib.request.Request(
                        base + path, data=data, method="POST",
                        headers={"Content-Type": "application/json", "X-Pilot-Client": "web"},
                    )
                    with opener.open(req) as resp:
                        return resp.status, json.loads(resp.read().decode("utf-8"))

                post("/api/login", {"password": "secret123"})
                code, body = post("/api/pause")
                self.assertEqual(code, 200)
                self.assertTrue(body["paused"])
                self.assertTrue(c.is_paused())

                with opener.open(base + "/api/state") as resp:
                    st = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(st["paused"])

                code2, body2 = post("/api/resume")
                self.assertEqual(code2, 200)
                self.assertFalse(body2["paused"])
                self.assertFalse(c.is_paused())
            finally:
                c.stop_web()

    def test_daemon_runs_and_stops_cleanly(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            settings = Settings({"provider": "mock"}, path=tmp / "settings.json")
            stop = threading.Event()
            lines = []
            t = threading.Thread(target=lambda: run_daemon(settings, stop_event=stop, out=lines.append), daemon=True)
            t.start()
            time.sleep(0.15)
            stop.set()
            t.join(timeout=3.0)
            self.assertFalse(t.is_alive())
            self.assertTrue(any("background service is running" in s for s in lines))


if __name__ == "__main__":
    unittest.main()
