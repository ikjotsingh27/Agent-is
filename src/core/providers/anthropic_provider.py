"""Anthropic Messages API, using only the Python standard library (nothing to install)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .base import Provider, ProviderError, Response, ToolCall

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


def _default_transport(url: str, headers: dict, body: bytes, timeout: float):
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return reply.status, reply.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProviderError("Could not reach the model service. Check your internet connection and try again.")


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, api_key: str, prompt_caching: bool = True, transport=None, timeout: float = 60.0):
        self._api_key = api_key
        self.prompt_caching = prompt_caching
        self._transport = transport or _default_transport
        self.timeout = timeout

    def _clean(self, text: str) -> str:
        if self._api_key:
            text = text.replace(self._api_key, "[hidden]")
        return text

    def complete(self, *, model, system, messages, tools=None, max_tokens=1024) -> Response:
        if not self._api_key:
            raise ProviderError("There is no API key yet. Paste one in Settings (or run 'python -m src setup').")
        cleaned_msgs = []
        for m in messages:
            c = m.get("content")
            if isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "input_audio" for b in c):
                kept = [b for b in c if not (isinstance(b, dict) and b.get("type") == "input_audio")]
                if not kept:
                    kept = [{"type": "text", "text": "(Audio attachment — switch to Gemini to hear voice notes.)"}]
                cleaned_msgs.append({**m, "content": kept})
            else:
                cleaned_msgs.append(m)
        payload = {"model": model, "max_tokens": max_tokens, "messages": cleaned_msgs}
        if system:
            if self.prompt_caching:
                payload["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
            else:
                payload["system"] = system
        if tools:
            payload["tools"] = tools
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        body = json.dumps(payload).encode("utf-8")
        status, raw = self._transport(API_URL, headers, body, self.timeout)
        if status != 200:
            raise ProviderError(self._friendly_error(status, raw))
        return self._parse(raw, model)

    def _friendly_error(self, status: int, raw: bytes) -> str:
        detail = ""
        try:
            detail = json.loads(raw.decode("utf-8", "replace")).get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            pass
        if status in (401, 403):
            msg = "The API key was rejected. Paste a new key in Settings (or run 'python -m src setup')."
        elif status == 404:
            msg = "The model name was not found. Pick another model in Settings (or run 'python -m src setup')."
        elif status == 429:
            msg = "The service is busy or a rate limit was reached. Wait a minute and try again."
        elif status == 400:
            msg = "The request was rejected" + (f": {detail}" if detail else ".")
        elif status >= 500:
            msg = "The model service had a problem. Try again in a moment."
        else:
            msg = f"The model service answered with an unexpected status ({status})."
        return self._clean(msg)

    def _parse(self, raw: bytes, model: str) -> Response:
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise ProviderError("The model service sent a reply that could not be read. Try again.")
        content, tool_calls, texts, thoughts = [], [], [], []
        for block in data.get("content", []):
            if block.get("type") == "text":
                content.append({"type": "text", "text": block.get("text", "")})
                texts.append(block.get("text", ""))
            elif block.get("type") == "thinking":
                thoughts.append(block.get("thinking", ""))
            elif block.get("type") == "tool_use":
                call = ToolCall(id=block["id"], name=block["name"], input=block.get("input", {}))
                tool_calls.append(call)
                content.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.input})
        stop_reason = data.get("stop_reason", "")
        text = "".join(texts)
        truncated = (stop_reason == "max_tokens") and (bool(tool_calls) or not text)
        if truncated:
            tool_calls = []
            content = [{"type": "text", "text": text}] if text else []
        usage = data.get("usage", {})
        return Response(
            text=text,
            content=content,
            tool_calls=tool_calls,
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
            cache_write_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
            model=data.get("model", model),
            stop_reason=stop_reason,
            truncated=truncated,
            reasoning="\n".join(t for t in thoughts if t).strip(),
        )
