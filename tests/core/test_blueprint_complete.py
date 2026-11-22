"""End-to-end blueprint completion tests: Approvals connector & cross-screen sync, new-email second check,
money/trading never-allowed block, router email/calendar rules, never-silent scheduler failures,
voice notes (recorder + input_audio), lazy skills, and Settings limits controls."""
from __future__ import annotations

import base64
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from src.core.agent_tools import ApprovalRequest
from src.core.approvals import ApprovalQueue, extract_email_recipient, second_check
from src.core.permissions import DEFAULT_POLICY, classify, decide
from src.core.providers.anthropic_provider import AnthropicProvider
from src.core.providers.openai_compatible import to_chat_messages
from src.core.router import pick_model
from src.core.scheduler import Scheduler
from src.core.settings import Settings
from src.core.skills import SkillStore
from src.core.voice import VoiceError, VoiceRecorder, encode_audio_block, is_audio_file
from src.ui.controller import Controller
from tests.helpers import FakeModelService, FakeTelegram, GOOD_TOKEN, make_agent, text_response


class ApprovalsAndPermissionsPolishTests(unittest.TestCase):
    def test_enqueue_sets_item_id_connector_and_fires_listener(self):
        q = ApprovalQueue()
        req = ApprovalRequest(action="Send email to bob@example.com\nSubject: Hi", reason="User asked",
                              risks=["sends email"], rule="", connector="Gmail")
        item = q.enqueue(req)
        self.assertEqual(req.item_id, item.id)
        self.assertEqual(item.connector, "Gmail")
        self.assertIn("new address (bob@example.com)", item.flag)

        fired = []
        q.add_listener(req.item_id, fired.append)
        self.assertTrue(q.resolve(item.id, "once"))
        self.assertEqual(fired, ["once"])

    def test_second_check_known_recipients_and_large_amounts(self):
        self.assertEqual(extract_email_recipient("Send email to Alice@Example.COM\nSubject: Hi"), "alice@example.com")
        flag_new = second_check("Send email to alice@example.com\nSubject: Hi", "Replying", known_recipients=[])
        self.assertIn("new address (alice@example.com)", flag_new)
        flag_known = second_check("Send email to alice@example.com\nSubject: Hi", "Replying",
                                  known_recipients=["alice@example.com"])
        self.assertEqual(flag_known, "")
        flag_big = second_check("python buy.py --purchase $450", "Buy item")
        self.assertIn("$450", flag_big)

    def test_controller_remembers_approved_email_recipient(self):
        with tempfile.TemporaryDirectory() as td:
            ctrl = Controller(Settings({"provider": "mock"}, path=Path(td) / "settings.json"),
                              provider_factory=FakeModelService, startup_dir=Path(td) / "startup")
            req = ApprovalRequest(action="Send email to friend@uni.edu\nSubject: Notes", reason="Share notes",
                                  connector="Gmail")
            item = ctrl.approvals.enqueue(req)
            self.assertIn("new address (friend@uni.edu)", item.flag)
            ctrl.resolve_approval(item.id, "once")
            self.assertIn("friend@uni.edu", ctrl.settings.get("known_recipients", default=[]))
            # Second time to same recipient has no new-address warning
            req2 = ApprovalRequest(action="Send email to friend@uni.edu\nSubject: Follow up", reason="Follow up",
                                   connector="Gmail")
            item2 = ctrl.approvals.enqueue(req2)
            self.assertEqual(item2.flag, "")

    def test_never_allowed_blocks_money_transfer_and_trading_even_when_all_allowed(self):
        all_allow = {k: "allow" for k in DEFAULT_POLICY}
        for cmd in (
            "python -c 'import ccxt; ccxt.binance().create_order()'",
            "python -c 'import stripe; stripe.Transfer.create(amount=500)'",
            "python -c 'web3.eth.send_transaction(tx)'",
        ):
            v = classify(cmd)
            self.assertTrue(v.blocked, f"Expected blocked for: {cmd}")
            self.assertEqual(decide(v, all_allow, []), "block")

    def test_router_sends_email_and_calendar_queries_to_main_model(self):
        s = Settings({"models": {"main": "big-model", "cheap": "tiny-model"}})
        self.assertEqual(pick_model("hi there", s), "tiny-model")
        self.assertEqual(pick_model("check my email", s), "big-model")
        self.assertEqual(pick_model("what is on my calendar today?", s), "big-model")


class SchedulerNeverSilentTests(unittest.TestCase):
    def test_crashing_job_reports_failure_never_silence_and_updates_last_run(self):
        with tempfile.TemporaryDirectory() as td:
            settings = Settings({}, path=Path(td) / "settings.json")
            now = [5_000.0]
            reports = []

            def boom(job):
                raise RuntimeError("site blocked request")

            sched = Scheduler(
                settings,
                run_job=boom,
                on_report=lambda job, text, quiet: reports.append((job.name, text, quiet)),
                save_settings=settings.save,
                clock=lambda: now[0],
            )
            sched.add_job("Price watch", "Check price", interval_minutes=10)
            now[0] += 700.0
            ran = sched.tick()
            self.assertEqual(len(ran), 1)
            self.assertEqual(len(reports), 1)
            self.assertFalse(reports[0][2])  # not quiet!
            self.assertIn("failed", reports[0][1].lower())
            self.assertIn("site blocked request", reports[0][1])
            # Immediate second tick does not crash-loop
            self.assertEqual(sched.tick(), [])

    def test_scheduled_report_notifies_connected_telegram_owner(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            tg = FakeTelegram()
            tg.user_says("hi", chat_id=42, first_name="Sam")
            settings = Settings({"provider": "mock"}, path=tmp / "settings.json")
            ctrl = Controller(
                settings, provider_factory=FakeModelService, usage_dir=tmp / "usage",
                startup_dir=tmp / "startup",
                connector_options={"telegram_transport": tg.transport, "telegram_poll_seconds": 1},
            )
            try:
                res = ctrl.check_connector("telegram", {"bot_token": GOOD_TOKEN})
                self.assertTrue(res.ok)
                job = ctrl.add_scheduled_job("Morning brief", "Give brief", interval_minutes=60)
                ctrl._on_schedule_report(job, "You have 1 meeting today.", quiet=False)
                self.assertTrue(any(chat == 42 and "Morning brief" in msg for chat, msg in tg.sent))
            finally:
                ctrl.stop_connectors()


class VoiceAndLazySkillsTests(unittest.TestCase):
    def test_voice_recorder_encoding_and_providers(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)

            # Fake MCI writes a tiny valid WAV header on 'save'
            def fake_mci(cmd: str):
                if cmd.startswith("save "):
                    out_p = Path(cmd.split('"')[1])
                    out_p.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
                return 0, ""

            rec = VoiceRecorder(mci_fn=fake_mci)
            rec.start()
            self.assertTrue(rec.recording)
            wav = rec.stop(tmp / "inbox")
            self.assertFalse(rec.recording)
            self.assertTrue(wav.is_file())
            self.assertTrue(is_audio_file(wav))

            block = encode_audio_block(wav)
            self.assertIsNotNone(block)
            self.assertEqual(block["type"], "input_audio")
            self.assertEqual(block["input_audio"]["format"], "wav")
            self.assertEqual(base64.b64decode(block["input_audio"]["data"]), b"RIFF\x24\x00\x00\x00WAVEfmt ")

            # OpenAI-compatible format preserves multimodal input_audio array
            chat_msgs = to_chat_messages("sys", [{"role": "user", "content": [{"type": "text", "text": "hear"}, block]}])
            self.assertIsInstance(chat_msgs[1]["content"], list)
            self.assertEqual(chat_msgs[1]["content"][1]["type"], "input_audio")

            # Anthropic strips input_audio gracefully instead of sending an invalid block
            sent_bodies = []

            def fake_anth(url, headers, body, timeout):
                sent_bodies.append(json.loads(body.decode("utf-8")))
                return 200, json.dumps({
                    "content": [{"type": "text", "text": "ok"}],
                    "usage": {"input_tokens": 5, "output_tokens": 2},
                    "stop_reason": "end_turn",
                }).encode("utf-8")

            anth = AnthropicProvider("sk-ant-test", transport=fake_anth)
            anth.complete(model="claude-sonnet-5-5", system="sys",
                          messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}, block]}])
            self.assertFalse(any(b.get("type") == "input_audio" for b in sent_bodies[0]["messages"][0]["content"]))

            # Agent sends media_blocks on call 1 ONLY and keeps Conversation.turns purely text
            agent, provider, *_ = make_agent([text_response("Heard you!"), text_response("Second turn")])
            r1 = agent.run_task("Voice note attached", media_blocks=[block])
            self.assertEqual(r1.text, "Heard you!")
            self.assertIsInstance(provider.calls[0]["messages"][-1]["content"], list)
            # Second turn does NOT resend base64 audio bytes!
            r2 = agent.run_task("Follow up")
            self.assertEqual(r2.text, "Second turn")
            self.assertNotIn("input_audio", json.dumps(provider.calls[1]["messages"]))

            # Missing microphone gives a clean VoiceError
            broken = VoiceRecorder(mci_fn=lambda cmd: (1, "No wave device"))
            with self.assertRaises(VoiceError):
                broken.start()

    def test_lazy_skills_zero_overhead_when_unmatched_and_injects_on_trigger(self):
        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            skills_dir.mkdir()
            (skills_dir / "essay-helper.md").write_text(
                "---\ntriggers: essay, thesis, bibliography\n---\nAlways use APA 7 citations.\n",
                encoding="utf-8",
            )
            store = SkillStore(skills_dir)
            agent, provider, *_ = make_agent([text_response("Hi!"), text_response("APA ready.")])
            agent.skills = store

            # Unmatched prompt: 0 skill text in system OR messages
            sys_before = agent.system_prompt()
            agent.run_task("hello there")
            self.assertNotIn("APA 7", sys_before)
            self.assertNotIn("APA 7", json.dumps(provider.calls[0]["messages"]))

            # Matched trigger: skill injected into user message while system prompt stays identical (caching!)
            agent.run_task("Help me outline my criminology essay")
            self.assertEqual(provider.calls[1]["system"], sys_before)
            self.assertIn("APA 7 citations", provider.calls[1]["messages"][-1]["content"])


class SettingsLimitsTests(unittest.TestCase):
    def test_save_limits_validates_and_persists(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctrl = Controller(Settings({"provider": "mock"}, path=tmp / "settings.json"),
                              provider_factory=FakeModelService, startup_dir=tmp / "startup")
            self.assertNotEqual(ctrl.save_limits("abc", "2.0", "5"), "")
            self.assertNotEqual(ctrl.save_limits("50", "2.0", "5"), "")
            self.assertEqual(ctrl.save_limits("500,000", "$3.50", "8"), "")
            cfg = ctrl.limits_config()
            self.assertEqual(cfg["daily_tokens"], 500000)
            self.assertEqual(cfg["daily_usd"], 3.5)
            self.assertEqual(cfg["max_disk_gb"], 8.0)
            self.assertEqual(ctrl.workspace.max_bytes, 8 * 1024 ** 3)


if __name__ == "__main__":
    unittest.main()
