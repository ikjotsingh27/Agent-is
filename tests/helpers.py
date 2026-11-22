"""Small builders shared by the tests."""
from __future__ import annotations

import itertools
import tempfile
from pathlib import Path

from src.core.agent import Agent
from src.core.providers.base import Response, ToolCall
from src.core.providers.mock_provider import MockProvider
from src.core.settings import Settings
from src.core.tools import ToolRegistry, builtin_tools
from src.core.usage import UsageTracker


def text_response(text="ok", input_tokens=10, output_tokens=5, **extra) -> Response:
    return Response(text=text, content=[{"type": "text", "text": text}], input_tokens=input_tokens,
                    output_tokens=output_tokens, model="claude-sonnet-5-5", stop_reason="end_turn", **extra)


def tool_response(*calls, input_tokens=10, output_tokens=5) -> Response:
    """calls are (id, name, input) tuples."""
    tool_calls = [ToolCall(i, n, inp) for i, n, inp in calls]
    content = [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in tool_calls]
    return Response(text="", content=content, tool_calls=tool_calls, input_tokens=input_tokens,
                    output_tokens=output_tokens, model="claude-sonnet-5-5", stop_reason="tool_use")


def make_agent(script=None, overrides=None, tools=None, provider=None):
    """Returns (agent, provider, usage, tmp_dir). Everything lives in a temp folder."""
    tmp = Path(tempfile.mkdtemp(prefix="pilot-test-"))
    settings = Settings(overrides or {}, path=tmp / "settings.json")
    provider = provider or MockProvider(script)
    usage = UsageTracker(settings, usage_dir=tmp / "usage")
    registry = ToolRegistry(tools if tools is not None else builtin_tools(),
                            settings.get("enabled_tools", default=[]))
    counter = itertools.count(1)
    agent = Agent(settings, provider, usage, registry=registry, clock=lambda: float(next(counter)),
                  tool_output_dir=tmp / "tool_output")
    return agent, provider, usage, tmp


# --- fakes shared by the UI tests and tools/walkthrough.py ------------------------------------------
GOOD_TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefghijklmn"


class FakeTelegram:
    """Stands in for the Telegram Bot API. Pass .transport to the connector; read .sent to see replies."""

    def __init__(self, token=GOOD_TOKEN, username="my_pilot_bot"):
        import threading
        self.token, self.username = token, username
        self.updates, self.sent, self.sent_docs, self.files, self.next_id = [], [], [], {}, 100
        self._lock = threading.Lock()

    def user_says(self, text, chat_id=42, first_name="Sam"):
        with self._lock:
            self.next_id += 1
            self.updates.append({"update_id": self.next_id, "message": {
                "message_id": self.next_id, "text": text, "chat": {"id": chat_id, "type": "private"},
                "from": {"id": chat_id, "first_name": first_name}}})

    def user_sends_document(self, filename, data: bytes, caption="", chat_id=42, first_name="Sam"):
        with self._lock:
            self.next_id += 1
            fid = f"file_{self.next_id}"
            self.files[fid] = (f"documents/{filename}", data)
            msg = {"message_id": self.next_id, "chat": {"id": chat_id, "type": "private"},
                   "from": {"id": chat_id, "first_name": first_name},
                   "document": {"file_id": fid, "file_name": filename, "file_size": len(data)}}
            if caption:
                msg["caption"] = caption
            self.updates.append({"update_id": self.next_id, "message": msg})

    def transport(self, url, body, timeout):
        import json
        import time
        token, method = url.split("/bot", 1)[1].split("/", 1)
        if token != self.token:
            return 401, json.dumps({"ok": False, "description": "Unauthorized"}).encode()
        params = json.loads(body.decode() or "{}")
        result = True
        if method == "getMe":
            result = {"id": 1, "is_bot": True, "username": self.username}
        elif method == "getUpdates":
            offset = params.get("offset")
            with self._lock:
                if offset is not None:
                    self.updates = [u for u in self.updates if u["update_id"] >= offset]
                result = list(self.updates)
            if not result and params.get("timeout"):
                time.sleep(0.02)  # a real long poll waits; keep test loops from spinning
        elif method == "getFile":
            file_path, data = self.files[params["file_id"]]
            result = {"file_id": params["file_id"], "file_path": file_path, "file_size": len(data)}
        elif method == "sendMessage":
            self.sent.append((params["chat_id"], params["text"]))
        return 200, json.dumps({"ok": True, "result": result}).encode()

    def multipart_transport(self, url, body, content_type, timeout):
        import json
        token, method = url.split("/bot", 1)[1].split("/", 1)
        if token != self.token:
            return 401, json.dumps({"ok": False, "description": "Unauthorized"}).encode()
        self.sent_docs.append((method, content_type, body))
        return 200, json.dumps({"ok": True, "result": {"message_id": 999}}).encode()

    def download_transport(self, url, timeout, max_bytes):
        remote = url.split(f"/file/bot{self.token}/", 1)[1]
        for path, data in self.files.values():
            if path == remote:
                return 200, data[:max_bytes + 1]
        return 404, b""


class FakeModelService:
    """Stands in for Gemini: a model list, and replies that echo the user. A key containing 'bad' is rejected."""

    def __init__(self, settings):
        self.key = settings.api_key()

    def list_models(self):
        from src.core.providers import ProviderError
        if "bad" in self.key:
            raise ProviderError("The API key was rejected.")
        return ["gemini-9.0-flash", "gemini-9.0-flash-lite"]

    def complete(self, **kw):
        from src.core.providers import ProviderError
        if "bad" in self.key:
            raise ProviderError("The API key was rejected.")
        last = kw["messages"][-1]["content"]
        # One scripted task, so the walkthrough can see an approval card: "tidy up" deletes old.txt.
        if isinstance(last, str) and last.endswith("tidy up"):
            return tool_response(("t1", "load_tool", {"names": ["run_command"]}))
        if isinstance(last, list) and last and last[0].get("tool_use_id") == "t1":
            return tool_response(("t2", "run_command", {"command": "Remove-Item old.txt",
                                                        "reason": "You asked me to tidy up, and old.txt is not needed."}))
        if isinstance(last, list) and last and last[0].get("tool_use_id") == "t2":
            return text_response("Tidy-up result: " + str(last[0].get("content", ""))[:80])
        # Second scripted task, so the walkthrough can check two-way file sharing in Chat.
        if isinstance(last, str) and last.endswith("share a file"):
            return tool_response(("f1", "load_tool", {"names": ["send_file"]}))
        if isinstance(last, list) and last and last[0].get("tool_use_id") == "f1":
            return tool_response(("f2", "send_file", {"path": "reply_note.txt", "note": "Made for you"}))
        if isinstance(last, list) and last and last[0].get("tool_use_id") == "f2":
            return text_response("Here is your file: reply_note.txt")
        return text_response("OK" if last == "Say OK." else f"Echo: {last}")