"""The one interface every model provider follows.

Messages use the common role/content shape: {"role": "user" | "assistant", "content": str or a list
of blocks}. A new provider translates this shape to its own service inside complete().
"""
from __future__ import annotations

from dataclasses import dataclass, field


class ProviderError(Exception):
    """A problem talking to the model service, worded for a non-technical reader."""


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class Response:
    text: str
    content: list  # assistant blocks, stored back into the conversation as-is
    tool_calls: list = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    model: str = ""
    stop_reason: str = ""
    truncated: bool = False  # True when output was cut off before producing a usable reply or valid tool call
    reasoning: str = ""  # optional model thinking/reasoning text when the provider returns it


class Provider:
    name = "base"

    def complete(self, *, model: str, system: str, messages: list, tools: list | None = None,
                 max_tokens: int = 1024) -> Response:
        raise NotImplementedError
