import tempfile
import unittest
from pathlib import Path

from src import cli
from src.core.settings import Settings
from tests.helpers import make_agent


def scripted(lines):
    it = iter(lines)
    return lambda prompt="": next(it)


class CliTests(unittest.TestCase):
    def test_chat_commands_and_reply(self):
        agent, *_ = make_agent()
        out = []
        cli.chat(agent.settings, agent, input_fn=scripted(["hello", "/usage", "/help", "/reset", "/nope", "/quit"]),
                 out=out.append)
        text = "\n".join(out)
        self.assertIn("Mock reply: hello", text)
        self.assertIn("Today: 1 calls", text)
        self.assertIn("Chat cleared.", text)
        self.assertIn("I don't know that command", text)

    def test_chat_asks_before_going_past_a_cap(self):
        agent, _, usage, _ = make_agent(overrides={"limits": {"daily_tokens": 1}})
        from tests.helpers import text_response
        usage.record(task="x", model="claude-sonnet-5-5", response=text_response(input_tokens=5), started=0, ended=1)
        out = []
        cli.chat(agent.settings, agent, input_fn=scripted(["hello", "n", "hello", "y", "/quit"]), out=out.append)
        text = "\n".join(out)
        self.assertIn("token limit is reached", text)
        self.assertIn("Mock reply: hello", text)  # second try, after saying yes

    def test_setup_saves_the_key_without_printing_it(self):
        path = Path(tempfile.mkdtemp(prefix="pilot-test-")) / "settings.json"
        settings = Settings(path=path)
        out = []
        cli.run_setup(settings, input_fn=scripted(["anthropic", "", "", "n"]),
                      secret_fn=lambda prompt="": "sk-test-1234567890", out=out.append)
        self.assertEqual(Settings.load(path).api_key("anthropic"), "sk-test-1234567890")
        self.assertNotIn("sk-test-1234567890", "\n".join(out))

    def test_gemini_setup_picks_free_models_and_tests_the_key(self):
        path = Path(tempfile.mkdtemp(prefix="pilot-test-")) / "settings.json"
        settings = Settings(path=path)

        class FakeGemini:
            def list_models(self):
                return ["gemini-9.0-flash", "gemini-9.0-flash-lite"]

            def complete(self, **kw):
                from tests.helpers import text_response
                return text_response("OK")

        out = []
        cli.run_setup(settings, input_fn=scripted(["python -m src check", "", "", "", ""]),
                      secret_fn=lambda prompt="": "AIza-test-1234567890", out=out.append,
                      provider_factory=lambda s: FakeGemini())
        saved = Settings.load(path)
        text = "\n".join(out)
        self.assertEqual(saved.provider, "gemini")
        self.assertEqual(saved.get("models", "main"), "gemini-9.0-flash-lite")
        self.assertEqual(saved.get("prices_per_million_tokens", "gemini-9.0-flash-lite", "input"), 0.0)
        self.assertIn("looks like a command", text)
        self.assertIn("Connected", text)
        self.assertNotIn("AIza-test-1234567890", text)

    def test_setup_refuses_a_key_or_command_as_model_name_and_cleans_old_junk(self):
        path = Path(tempfile.mkdtemp(prefix="pilot-test-")) / "settings.json"
        settings = Settings({"provider": "gemini",
                             "models": {"main": "python -m src check", "cheap": "python -m src check"},
                             "prices_per_million_tokens": {"python -m src check": {"input": 0, "output": 0},
                                                           "AQ.oldpastedkey123456789012345678901234": {"input": 0}}},
                            path=path)
        out = []
        key = "AQ.newkey-1234567890-abcdefghijklmnop"
        cli.run_setup(settings, input_fn=scripted(["", key, "python -m src check", "", "", "n"]),
                      secret_fn=lambda prompt="": key, out=out.append,
                      provider_factory=lambda s: (_ for _ in ()).throw(cli.ProviderError("offline")))
        saved = Settings.load(path)
        text = "\n".join(out)
        self.assertEqual(saved.get("models", "main"), "gemini-flash-lite-latest")
        self.assertIn("looks like an API key", text)
        self.assertNotIn(key, text)
        prices = saved.get("prices_per_million_tokens")
        self.assertNotIn("python -m src check", prices)
        self.assertNotIn("AQ.oldpastedkey123456789012345678901234", prices)
        self.assertIn("claude-sonnet-5-5", prices)

    def test_offline_check_passes(self):
        agent, *_ = make_agent()
        out = []
        self.assertEqual(cli.run_check(agent.settings, agent, offline=True, out=out.append), 0)
        self.assertIn("OK", "\n".join(out))


if __name__ == "__main__":
    unittest.main()
