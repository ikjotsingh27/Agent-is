"""Lazy tool loading.

The model starts with a short list of tool names and one line each (in the system prompt) plus a
single tiny 'load_tool' tool. A tool's full definition is sent only after the model asks for that
tool by name. With no tools enabled, no tool definitions are sent at all, so a plain reply costs
one small call. Tools are enabled per connector in settings ("enabled_tools").
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

LOAD_TOOL = {
    "name": "load_tool",
    "description": "Load the full definitions of tools from the list in the system prompt, so you can call them. "
                   "Load every tool you expect to need in one call.",
    "input_schema": {
        "type": "object",
        "properties": {"names": {"type": "array", "items": {"type": "string"}, "description": "Tool names from the list."}},
        "required": ["names"],
    },
}


@dataclass
class Tool:
    name: str
    one_line: str
    description: str
    input_schema: dict
    func: Callable[[dict], str]


class ToolRegistry:
    def __init__(self, tools: list, enabled: list | None = None):
        self._tools = {t.name: t for t in tools}
        self.enabled = list(enabled or [])

    def available(self) -> list:
        return [self._tools[n] for n in self.enabled if n in self._tools]

    def names(self) -> list:
        return [t.name for t in self.available()]

    def catalog_text(self) -> str:
        tools = self.available()
        if not tools:
            return ""
        lines = ["Tools you can use. First call load_tool with the names you need, then call them:"]
        lines += [f"- {t.name}: {t.one_line}" for t in tools]
        return "\n".join(lines)

    def definition(self, name: str) -> dict:
        tool = self._tools[name]
        return {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}

    def run(self, name: str, args: dict) -> str:
        if name not in self.names():
            raise KeyError(name)
        return str(self._tools[name].func(args or {}))


def _current_time(_args: dict) -> str:
    return datetime.now().astimezone().strftime("%A %Y-%m-%d %H:%M %Z")


def builtin_tools() -> list:
    """A tiny built-in tool that proves lazy loading works. Off unless listed in enabled_tools."""
    return [
        Tool(
            name="current_time",
            one_line="the current local date and time",
            description="Returns the current local date and time on the user's computer.",
            input_schema={"type": "object", "properties": {}},
            func=_current_time,
        )
    ]
