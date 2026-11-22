import json
import tempfile
import unittest
from pathlib import Path

from src.core.settings import Settings, SettingsError


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pilot-test-"))

    def test_defaults(self):
        s = Settings()
        self.assertEqual(s.provider, "gemini")  # free tier is the default since Session 1 follow-up
        self.assertEqual(s.get("models", "main"), "gemini-flash-lite-latest")
        self.assertEqual(s.get("enabled_tools"), [])

    def test_partial_override_keeps_other_defaults(self):
        s = Settings({"limits": {"max_calls_per_task": 3}})
        self.assertEqual(s.get("limits", "max_calls_per_task"), 3)
        self.assertEqual(s.get("limits", "max_output_tokens"), 1024)

    def test_save_and_load_roundtrip(self):
        path = self.tmp / "settings.json"
        s = Settings(path=path)
        s.set_api_key("anthropic", "  sk-test-1234567890  ")
        s.save()
        loaded = Settings.load(path)
        self.assertEqual(loaded.api_key("anthropic"), "sk-test-1234567890")

    def test_missing_file_gives_defaults(self):
        self.assertEqual(Settings.load(self.tmp / "nope.json").provider, "gemini")

    def test_bad_json_gives_friendly_error(self):
        path = self.tmp / "settings.json"
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(SettingsError):
            Settings.load(path)

    def test_key_is_never_shown(self):
        s = Settings({"api_keys": {"anthropic": "sk-test-1234567890"}})
        self.assertNotIn("sk-test-1234567890", repr(s))
        self.assertEqual(s.redact("oops sk-test-1234567890 leaked"), "oops [hidden] leaked")

    def test_saved_file_is_valid_json(self):
        path = self.tmp / "settings.json"
        Settings(path=path).save()
        self.assertIn("provider", json.loads(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
