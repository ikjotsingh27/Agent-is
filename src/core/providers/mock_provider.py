"""A fake provider for tests and for trying the app with no API key."""
from __future__ import annotations

from ..tokens import estimate_tokens
from .base import Provider, Response


class MockProvider(Provider):
    name = "mock"

    def __init__(self, script: list | None = None):
        self.script = list(script or [])
        self.calls: list = []  # every request received, for tests to inspect

    def complete(self, *, model, system, messages, tools=None, max_tokens=1024) -> Response:
        self.calls.append({"model": model, "system": system, "messages": messages,
                           "tools": tools, "max_tokens": max_tokens})
        if self.script:
            item = self.script.pop(0)
            return item(self.calls[-1]) if callable(item) else item
        last_text = ""
        for message in reversed(messages):
            if message["role"] == "user" and isinstance(message["content"], str):
                last_text = message["content"]
                break
        reply = f"Mock reply: {last_text}"
        return Response(
            text=reply,
            content=[{"type": "text", "text": reply}],
            input_tokens=estimate_tokens(system) + estimate_tokens(messages) + estimate_tokens(tools),
            output_tokens=estimate_tokens(reply),
            model=model,
            stop_reason="end_turn",
        )
