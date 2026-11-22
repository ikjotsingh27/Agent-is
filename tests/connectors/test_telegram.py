import tempfile
import time
import unittest
from pathlib import Path

from src.connectors import load_connectors
from src.connectors.telegram import test as telegram_test
from src.connectors.telegram.connector import TelegramClient, TelegramRunner
from src.core.agent_tools import SharedFile
from src.core.settings import Settings
from src.core.workspace import Workspace
from src.ui.controller import Controller
from tests.helpers import GOOD_TOKEN, FakeModelService, FakeTelegram


def wait_for(condition, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


class TelegramCheckTests(unittest.TestCase):
    def setUp(self):
        self.tg = FakeTelegram()

    def run_check(self, token=GOOD_TOKEN, saved=None):
        return telegram_test.run(token, saved or {}, transport=self.tg.transport)

    def test_wrong_shape_is_caught_before_calling_telegram(self):
        r = self.run_check("AIza-this-is-a-google-key")
        self.assertFalse(r.ok)
        self.assertIn("does not look like a bot token", r.message)

    def test_rejected_token(self):
        r = self.run_check("123456789:AAWrongTokenWrongTokenWrongToken_xyz")
        self.assertFalse(r.ok)
        self.assertIn("did not accept", r.message)
        self.assertIn("BotFather", r.fix)

    def test_no_message_yet_says_what_to_do(self):
        r = self.run_check()
        self.assertFalse(r.ok)
        self.assertIn("@my_pilot_bot", r.message)
        self.assertIn("t.me/my_pilot_bot", r.fix)

    def test_pass_learns_the_owner_and_says_hello(self):
        self.tg.user_says("hi", chat_id=42, first_name="Sam")
        r = self.run_check()
        self.assertTrue(r.ok, r.message)
        self.assertEqual(r.data["owner_chat_id"], 42)
        self.assertEqual(r.data["offset"], self.tg.next_id + 1)  # the setup 'hi' is not answered again
        self.assertEqual(r.secrets, {"bot_token": GOOD_TOKEN})
        self.assertNotIn("bot_token", r.data)
        self.assertEqual(self.tg.sent[0][0], 42)
        self.assertIn("Sam", r.message)

    def test_retest_keeps_the_saved_owner(self):
        r = self.run_check(saved={"owner_chat_id": 7, "owner_name": "Sam", "bot_username": "my_pilot_bot", "offset": 50})
        self.assertTrue(r.ok)
        self.assertEqual((r.data["owner_chat_id"], r.data["offset"]), (7, 50))

    def test_registry_finds_telegram(self):
        self.assertIn("telegram", load_connectors())


class TelegramRunnerTests(unittest.TestCase):
    def test_answers_only_the_owner_and_handles_start_and_photos(self):
        tg = FakeTelegram()
        asked, events, saved = [], [], []
        runner = TelegramRunner(TelegramClient(GOOD_TOKEN, transport=tg.transport), 42, None,
                                ask=lambda text: asked.append(text) or f"Echo: {text}",
                                on_event=lambda kind, info: events.append((kind, info)),
                                save_data=saved.append, poll_seconds=1)
        tg.user_says("/start", chat_id=42)
        tg.user_says("hello from a stranger", chat_id=99)
        tg.updates.append({"update_id": 500, "message": {"chat": {"id": 42, "type": "private"}, "photo": [{}]}})
        tg.next_id = 500
        tg.user_says("what time is it?", chat_id=42)
        runner.start()
        try:
            self.assertTrue(wait_for(lambda: asked == ["what time is it?"] and len(tg.sent) >= 3))
        finally:
            runner.stop()
        texts = [t for chat, t in tg.sent]
        self.assertTrue(all(chat == 42 for chat, _ in tg.sent))  # never a word to the stranger
        self.assertIn("Hi! I'm Pilot", texts[0])
        self.assertIn("text messages", texts[1])
        self.assertEqual(texts[2], "Echo: what time is it?")
        self.assertEqual(saved[-1], {"offset": 502})
        self.assertIn(("message", ("what time is it?", "Echo: what time is it?")), events)

    def test_long_replies_are_split(self):
        tg = FakeTelegram()
        TelegramClient(GOOD_TOKEN, transport=tg.transport).send_message(42, "x" * 9000)
        self.assertEqual([len(t) for _, t in tg.sent], [4000, 4000, 1000])

    def test_two_way_file_transfer_on_telegram(self):
        tg = FakeTelegram()
        ws = Workspace(Settings({}), root=Path(tempfile.mkdtemp(prefix="pilot-tg-ws-")) / "agent")
        out_path = ws.files / "report.pdf"
        out_path.write_bytes(b"%PDF-1.4\nout")
        shared = SharedFile(path=out_path, rel_path="report.pdf", name="report.pdf", size=12, note="Finished report")
        asked, events = [], []

        def ask(text, attachments=None):
            asked.append((text, [p.name for p in (attachments or [])]))
            return "All set!", [shared]

        client = TelegramClient(GOOD_TOKEN, transport=tg.transport,
                                multipart_transport=tg.multipart_transport,
                                download_transport=tg.download_transport)
        runner = TelegramRunner(client, 42, None, ask=ask,
                                on_event=lambda k, i: events.append((k, i)),
                                save_data=lambda d: None, poll_seconds=1, workspace=ws)
        tg.user_sends_document("input.csv", b"col1,col2\n1,2\n", caption="make a report", chat_id=42)
        runner.start()
        try:
            self.assertTrue(wait_for(lambda: len(tg.sent_docs) == 1))
        finally:
            runner.stop()
        self.assertEqual(asked, [("make a report", ["input.csv"])])
        self.assertEqual((ws.files / "inbox" / "input.csv").read_bytes(), b"col1,col2\n1,2\n")
        method, ctype, raw = tg.sent_docs[0]
        self.assertEqual(method, "sendDocument")
        self.assertIn("multipart/form-data", ctype)
        self.assertIn(b'filename="report.pdf"', raw)
        self.assertIn(b"%PDF-1.4\nout", raw)
        self.assertTrue(any(k == "message" and len(i) == 3 and i[2] == [shared] for k, i in events))


class ControllerConnectorTests(unittest.TestCase):
    def make(self, values=None):
        self.tg = FakeTelegram()
        tmp = Path(tempfile.mkdtemp(prefix="pilot-tg-test-"))
        base = {"api_keys": {"gemini": "AIza-good-key-1234567890"}}
        settings = Settings({**base, **(values or {})}, path=tmp / "settings.json")
        c = Controller(settings, provider_factory=FakeModelService, usage_dir=tmp / "usage",
                       connector_options={"telegram_transport": self.tg.transport, "telegram_poll_seconds": 1})
        self.addCleanup(c.stop_connectors)
        return c, tmp

    def test_setup_end_to_end_then_phone_chat(self):
        c, tmp = self.make()
        self.assertEqual(c.connector_status("telegram"), "Not set up")
        self.assertFalse(c.check_connector("telegram", {"bot_token": " " + GOOD_TOKEN + "\n"}).ok)  # no message yet
        self.assertEqual(c.connector_status("telegram"), "Not set up")
        self.tg.user_says("hi")
        result = c.check_connector("telegram", {"bot_token": GOOD_TOKEN})
        self.assertTrue(result.ok, result.message)
        self.assertEqual(c.connector_status("telegram"), "Connected")
        saved = Settings.load(tmp / "settings.json")
        self.assertEqual(saved.get("connector_secrets", "telegram", "bot_token"), GOOD_TOKEN)
        self.assertEqual(saved.redact(f"oops {GOOD_TOKEN}"), "oops [hidden]")
        self.tg.user_says("hello from my phone")
        self.assertTrue(wait_for(lambda: ("42", "Echo: hello from my phone") in [(str(a), b) for a, b in self.tg.sent]))
        self.assertIn("hello from my phone", str(c.agent.conversation.window()))  # same chat as the window

    def test_turn_off_and_on(self):
        c, _ = self.make()
        self.tg.user_says("hi")
        c.check_connector("telegram", {"bot_token": GOOD_TOKEN})
        c.set_connector_enabled("telegram", False)
        self.assertEqual(c.connector_status("telegram"), "Off")
        self.assertNotIn("telegram", c.runners)
        c.set_connector_enabled("telegram", True)
        self.assertIn("telegram", c.runners)

    def test_cannot_turn_on_before_a_passing_test(self):
        c, _ = self.make()
        c.set_connector_enabled("telegram", True)
        self.assertEqual(c.connector_status("telegram"), "Not set up")

    def test_daily_limit_from_the_phone_is_not_overridden(self):
        c, _ = self.make({"limits": {"daily_tokens": 1}})
        c.send("use some tokens")
        reply = c._ask_from_connector("one more")
        self.assertIn("Open Pilot on your computer", reply)
        self.assertFalse(c.agent.cap_override)


if __name__ == "__main__":
    unittest.main()
