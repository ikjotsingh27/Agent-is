import http.cookiejar
import json
import socket
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from src.core.providers.base import Response
from src.core.sessions import SessionStore
from src.core.settings import Settings
from src.core.web_server import PilotWebServer, hash_password, verify_password
from src.ui.controller import Controller
from tests.helpers import FakeModelService, make_agent, text_response, tool_response


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(condition, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


class SavedSessionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pilot-sess-test-"))
        self.settings_path = self.tmp / "settings.json"

    def test_multiple_sessions_switch_delete_and_survive_restart(self):
        c1 = Controller(Settings({"provider": "mock"}, path=self.settings_path))
        r1 = c1.send("First session question")
        self.assertEqual(r1.kind, "ok")
        first_id = c1.active_session_id

        # Start a new session without losing the first one
        c1.new_session()
        second_id = c1.active_session_id
        self.assertNotEqual(first_id, second_id)
        c1.send("Second session question")

        sessions = c1.list_sessions()
        self.assertEqual(len(sessions), 2)
        titles = {s.id: s.title for s in sessions}
        self.assertIn("First session question", titles[first_id])
        self.assertIn("Second session question", titles[second_id])

        # Switch back to the first session
        self.assertTrue(c1.select_session(first_id))
        self.assertIn("First session question", str(c1.agent.conversation.window()))
        self.assertNotIn("Second session question", str(c1.agent.conversation.window()))

        # Survives restart (new Controller on the same settings file)
        c2 = Controller(Settings({"provider": "mock"}, path=self.settings_path))
        self.assertEqual(c2.active_session_id, first_id)
        self.assertEqual(len(c2.list_sessions()), 2)
        self.assertEqual(c2.saved_transcript()[0][1], "First session question")

        # Delete active session falls back to remaining session
        self.assertTrue(c2.delete_session(first_id))
        self.assertEqual(c2.active_session_id, second_id)
        self.assertEqual(len(c2.list_sessions()), 1)

    def test_migrates_legacy_chat_history_file(self):
        mem_dir = self.tmp / "memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        (mem_dir / ".chat_history.json").write_text(json.dumps({
            "turns": [[{"role": "user", "content": "Old chat before sessions"},
                       {"role": "assistant", "content": [{"type": "text", "text": "Old answer"}]}]],
            "summary_lines": [],
        }), encoding="utf-8")
        c = Controller(Settings({"provider": "mock"}, path=self.settings_path))
        self.assertEqual(len(c.list_sessions()), 1)
        self.assertIn("Old chat before sessions", c.list_sessions()[0].title)
        self.assertEqual(c.saved_transcript()[0][1], "Old chat before sessions")


class ThinkingStepsTests(unittest.TestCase):
    def test_agent_emits_live_steps_and_captures_reasoning(self):
        events = []
        script = [
            tool_response(("t1", "load_tool", {"name": "current_time"})),
            tool_response(("t2", "current_time", {})),
            Response(text="It is 7 PM.", content=[{"type": "text", "text": "It is 7 PM."}],
                     input_tokens=10, output_tokens=5, reasoning="Checked the local clock and formatted it."),
        ]
        agent, *_ = make_agent(script, {"enabled_tools": ["current_time"]})
        agent.on_step = events.append
        res = agent.run_task("what time is it?")
        self.assertEqual(res.text, "It is 7 PM.")
        # Should record live running -> done transitions for thinking, load_tool, current_time, final reply
        self.assertTrue(any(e["status"] == "running" and "Thinking" in e["label"] for e in events))
        self.assertTrue(any(e["status"] == "done" and "Loading tools: current_time" in e["label"] for e in events))
        self.assertTrue(any(e["status"] == "done" and "Running current_time" in e["label"] for e in events))
        self.assertGreaterEqual(len(res.steps), 5)
        self.assertIn("Checked the local clock", res.steps[-1]["detail"])

    def test_controller_saves_steps_into_session_transcript(self):
        tmp = Path(tempfile.mkdtemp(prefix="pilot-steps-ctrl-"))
        c = Controller(Settings({"provider": "mock"}, path=tmp / "settings.json"))
        reply = c.send("hello steps")
        self.assertGreaterEqual(len(reply.steps), 1)
        self.assertEqual(reply.steps[-1]["status"], "done")
        # Reloaded session keeps the saved steps
        c2 = Controller(Settings({"provider": "mock"}, path=tmp / "settings.json"))
        rows = c2.saved_transcript()
        self.assertEqual(rows[1][0], "pilot")
        self.assertGreaterEqual(len(rows[1][4]), 1)


class PasswordProtectedWebServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pilot-web-test-"))
        self.port = free_port()
        settings = Settings({"provider": "mock"}, path=self.tmp / "settings.json")
        self.controller = Controller(settings, provider_factory=FakeModelService, usage_dir=self.tmp / "usage")
        self.addCleanup(self.controller.stop_web)
        self.base = f"http://127.0.0.1:{self.port}"
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def req(self, method: str, path: str, body=None, headers: dict | None = None):
        hdrs = dict(headers or {})
        data = None
        if isinstance(body, dict):
            data = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        elif isinstance(body, bytes):
            data = body
        request = urllib.request.Request(self.base + path, data=data, headers=hdrs, method=method)
        try:
            with self.opener.open(request, timeout=5) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as err:
            return err.code, err.read(), dict(err.headers)

    def test_password_hashing_and_web_access_end_to_end(self):
        # Cannot turn on without setting a password first
        err = self.controller.configure_web(enabled=True, host="127.0.0.1", port=self.port, new_password="")
        self.assertIn("password", err.lower())
        self.assertFalse(self.controller.web_config()["running"])

        # Short password refused
        err2 = self.controller.configure_web(enabled=True, host="127.0.0.1", port=self.port, new_password="12")
        self.assertIn("at least 4", err2)

        # Valid password starts server; plaintext password is NOT stored in settings.json
        err3 = self.controller.configure_web(enabled=True, host="127.0.0.1", port=self.port, new_password="my-secret-pass")
        self.assertEqual(err3, "")
        self.assertTrue(self.controller.web_config()["running"])
        raw_settings = (self.tmp / "settings.json").read_text(encoding="utf-8")
        self.assertNotIn("my-secret-pass", raw_settings)

        # Home page loads HTML; API blocks unauthenticated callers
        st, html_bytes, _ = self.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn(b"Pilot", html_bytes)
        st, _, _ = self.req("GET", "/api/state")
        self.assertEqual(st, 401)

        # CSRF check blocks POST without X-Pilot-Client: web
        st, _, _ = self.req("POST", "/api/login", {"password": "my-secret-pass"})
        self.assertEqual(st, 403)

        # Wrong password rejected
        st, _, _ = self.req("POST", "/api/login", {"password": "wrong"}, {"X-Pilot-Client": "web"})
        self.assertEqual(st, 401)

        # Right password succeeds and sets HttpOnly SameSite=Strict cookie
        st, _, hdrs = self.req("POST", "/api/login", {"password": "my-secret-pass"}, {"X-Pilot-Client": "web"})
        self.assertEqual(st, 200)
        self.assertIn("HttpOnly", hdrs.get("Set-Cookie", ""))
        self.assertIn("SameSite=Strict", hdrs.get("Set-Cookie", ""))

        # Upload a file, download it back, and confirm path traversal is blocked
        st, up_raw, _ = self.req(
            "POST", "/api/upload", b"hello binary",
            {"X-Pilot-Client": "web", "X-File-Name": "from_browser.txt", "Content-Type": "application/octet-stream"},
        )
        self.assertEqual(st, 200)
        up = json.loads(up_raw.decode())
        self.assertEqual(up["rel_path"], "inbox/from_browser.txt")

        st, dl_bytes, _ = self.req("GET", "/api/files?path=inbox/from_browser.txt")
        self.assertEqual(st, 200)
        self.assertEqual(dl_bytes, b"hello binary")

        st, _, _ = self.req("GET", "/api/files?path=../../settings.json")
        self.assertEqual(st, 403)

        # Send a chat message from the web UI and verify saved state + steps
        st, _, _ = self.req("POST", "/api/chat", {"text": "hello from browser"}, {"X-Pilot-Client": "web"})
        self.assertEqual(st, 200)
        self.assertTrue(wait_for(lambda: not self.controller.web_server._web_busy and len(self.controller.active_display_messages()) >= 2))
        st, state_raw, _ = self.req("GET", "/api/state")
        self.assertEqual(st, 200)
        state = json.loads(state_raw.decode())
        self.assertEqual(state["messages"][0]["text"], "hello from browser")
        self.assertGreaterEqual(len(state["messages"][1]["steps"]), 1)

        # Save memory note and clear chat via web API (no stacked sessions sidebar)
        st, _, _ = self.req("POST", "/api/memory/save", {"title": "Web Note", "content": "Saved from phone"}, {"X-Pilot-Client": "web"})
        self.assertEqual(st, 200)
        self.assertEqual(self.controller.memory_notes()[0].title, "Web Note")

        st, _, _ = self.req("POST", "/api/chat/clear", {}, {"X-Pilot-Client": "web"})
        self.assertEqual(st, 200)
        self.assertEqual(self.controller.active_display_messages(), [])
        self.assertEqual(len(self.controller.list_sessions()), 1)

        # Logout invalidates session cookie
        st, _, _ = self.req("POST", "/api/logout", {}, {"X-Pilot-Client": "web"})
        self.assertEqual(st, 200)
        st, _, _ = self.req("GET", "/api/state")
        self.assertEqual(st, 401)

    def test_brute_force_lockout(self):
        self.controller.configure_web(enabled=True, host="127.0.0.1", port=self.port, new_password="correct-password")
        for _ in range(5):
            st, _, _ = self.req("POST", "/api/login", {"password": "bad"}, {"X-Pilot-Client": "web"})
            self.assertEqual(st, 401)
        st, body, _ = self.req("POST", "/api/login", {"password": "correct-password"}, {"X-Pilot-Client": "web"})
        self.assertEqual(st, 429)
        self.assertIn(b"Too many wrong passwords", body)

    def test_password_hash_helper(self):
        salt, digest = hash_password("pilot-1234")
        self.assertTrue(verify_password("pilot-1234", salt, digest))
        self.assertFalse(verify_password("wrong", salt, digest))


if __name__ == "__main__":
    unittest.main()
