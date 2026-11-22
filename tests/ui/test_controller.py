import tempfile
import unittest
from pathlib import Path

from src.core.providers import ProviderError
from src.core.settings import Settings
from src.ui.controller import Controller
from tests.helpers import text_response

GOOD = "AIza-good-key-1234567890"
BAD = "AIza-bad-key-1234567890"


class FakeService:
    """Stands in for Gemini: a model list, and replies that echo the user. A 'bad' key is rejected."""
    def __init__(self, settings):
        self.key = settings.api_key()
        self.calls = []

    def list_models(self):
        if self.key == BAD:
            raise ProviderError(f"The API key was rejected. ({self.key})")
        return ["gemini-9.0-flash", "gemini-9.0-flash-lite"]

    def complete(self, **kw):
        if self.key == BAD:
            raise ProviderError("The API key was rejected.")
        last = kw["messages"][-1]["content"]
        return text_response("OK" if last == "Say OK." else f"Echo: {last}")


def make(values=None, factory=FakeService):
    tmp = Path(tempfile.mkdtemp(prefix="pilot-ui-test-"))
    settings = Settings(values or {}, path=tmp / "settings.json")
    return Controller(settings, provider_factory=factory, usage_dir=tmp / "usage"), tmp


class ControllerTests(unittest.TestCase):
    def test_new_user_sees_welcome(self):
        c, _ = make()
        self.assertTrue(c.needs_welcome())
        self.assertNotIn("mock", c.providers)

    def test_check_key_finds_models_and_does_not_save(self):
        c, tmp = make()
        check = c.check_key("gemini", "  " + GOOD + "\n")
        self.assertTrue(check.ok, check.message)
        self.assertEqual(check.main, "gemini-9.0-flash-lite")
        self.assertIn("gemini-9.0-flash", check.models)
        self.assertFalse((tmp / "settings.json").exists())
        self.assertTrue(c.needs_welcome())

    def test_bad_key_is_a_plain_message_without_the_key(self):
        c, _ = make()
        check = c.check_key("gemini", BAD)
        self.assertFalse(check.ok)
        self.assertIn("rejected", check.message)
        self.assertNotIn(BAD, check.message)
        self.assertFalse(c.check_key("gemini", "").ok)

    def test_save_switches_at_once_and_chat_works(self):
        c, tmp = make()
        self.assertEqual(c.save("gemini", GOOD, "gemini-9.0-flash-lite", "gemini-9.0-flash-lite"), "")
        self.assertFalse(c.needs_welcome())
        saved = Settings.load(tmp / "settings.json")
        self.assertEqual(saved.api_key("gemini"), GOOD)
        self.assertEqual(saved.get("prices_per_million_tokens", "gemini-9.0-flash-lite", "input"), 0.0)
        reply = c.send("hello")
        self.assertEqual((reply.kind, reply.text), ("ok", "Echo: hello"))
        self.assertIn("1 call", reply.detail)
        self.assertIn("Today: 1 calls", c.usage_line())

    def test_chat_is_kept_when_settings_change(self):
        c, _ = make({"api_keys": {"gemini": GOOD}})
        c.send("first")
        before = c.agent.conversation
        self.assertEqual(c.save("gemini", "", "gemini-9.0-flash", "gemini-9.0-flash-lite"), "")
        self.assertIs(c.agent.conversation, before)
        self.assertEqual(c.settings.api_key("gemini"), GOOD)  # empty key field keeps the saved key

    def test_save_refuses_bad_model_names_and_keys_in_model_fields(self):
        c, _ = make()
        self.assertIn("no spaces", c.save("gemini", GOOD, "python -m src check", "gemini-x"))
        self.assertIn("API key", c.save("gemini", GOOD, GOOD, "gemini-x"))
        self.assertIn("Paste your key", c.save("gemini", "", "gemini-x", "gemini-x"))
        self.assertTrue(c.needs_welcome())

    def test_errors_and_caps_come_back_as_replies(self):
        c, _ = make({"api_keys": {"gemini": BAD}})
        reply = c.send("hi")
        self.assertEqual(reply.kind, "error")
        c2, _ = make({"api_keys": {"gemini": GOOD}, "limits": {"daily_tokens": 1}})
        c2.send("one")
        self.assertEqual(c2.send("two").kind, "cap")
        self.assertEqual(c2.send("two", allow_over_cap=True).kind, "ok")

    def test_theme_is_saved(self):
        c, tmp = make()
        self.assertEqual(c.theme, "dark")
        c.set_theme("light")
        self.assertEqual(Settings.load(tmp / "settings.json").get("ui", "theme"), "light")


class WindowSmokeTests(unittest.TestCase):
    """Builds the real window once (no clicking), to catch mistakes in the screens. Skipped without a display."""

    def setUp(self):
        import tkinter
        try:
            self.root = tkinter.Tk()
            self.root.withdraw()
        except tkinter.TclError:
            self.skipTest("no display")

    def tearDown(self):
        if getattr(self, "app", None):
            self.app.close()  # also stops its timer, so it cannot fire into the next test
        else:
            self.root.destroy()

    def test_welcome_then_main_window_in_both_themes(self):
        from src.ui.app import App
        c, _ = make()
        app = self.app = App(c, root=self.root)
        self.assertIsNotNone(app.welcome)
        app.welcome.step_key("gemini")
        app.welcome.step_done("gemini-9.0-flash-lite")
        c.save("gemini", GOOD, "gemini-9.0-flash-lite", "gemini-9.0-flash-lite")
        app.finish_welcome("Hello!")
        self.assertEqual(app.views["Chat"].input.get("1.0", "end").strip(), "Hello!")
        app.views["Chat"].add("pilot", "hi", "1 call")
        app.show("Settings")
        app.change_theme("light")
        self.assertEqual(app.theme.name, "light")
        self.assertEqual(len(app.transcript), 1)
        self.root.update_idletasks()


if __name__ == "__main__":
    unittest.main()
