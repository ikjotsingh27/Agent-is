"""Services that speak the OpenAI "chat completions" format, using only the Python standard library.

Google Gemini (free tier, no card needed) is the first one. Groq, OpenRouter and Ollama use the same
format, so each of them can be added later as one more entry in providers/__init__.py.

The rest of Pilot keeps messages in the common block shape (see base.py). This file translates that
shape to the chat completions format on the way out, and back again on the way in.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from .base import Provider, ProviderError, Response, ToolCall

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


def _default_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float):
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return reply.status, reply.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProviderError("Could not reach the model service. Check your internet connection and try again.")


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def to_chat_messages(system: str, messages: list) -> list:
    """Common block messages -> chat completions messages."""
    out = [{"role": "system", "content": system}] if system else []
    for msg in messages:
        content = msg.get("content")
        if msg["role"] == "assistant":
            item = {"role": "assistant", "content": _text_of(content) or None}
            calls = []
            for b in content if isinstance(content, list) else []:
                if b.get("type") == "tool_use":
                    call = {"id": b["id"], "type": "function",
                            "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})}}
                    if b.get("extra_content"):  # Gemini's thought signature must be sent back unchanged
                        call["extra_content"] = b["extra_content"]
                    calls.append(call)
            if calls:
                item["tool_calls"] = calls
            out.append(item)
            continue
        if isinstance(content, list):
            for b in content:
                if b.get("type") == "tool_result":
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": _text_of(b.get("content"))})
            audio_parts = [b for b in content if isinstance(b, dict) and b.get("type") == "input_audio"]
            text = _text_of(content)
            if audio_parts:
                parts = []
                if text:
                    parts.append({"type": "text", "text": text})
                parts.extend(audio_parts)
                out.append({"role": "user", "content": parts})
            elif text:
                out.append({"role": "user", "content": text})
        else:
            out.append({"role": "user", "content": content or ""})
    return out


def to_chat_tools(tools: list) -> list:
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                              "parameters": t.get("input_schema") or {"type": "object"}}}
            for t in tools]


def pick_models(model_ids: list) -> dict:
    """Choose sensible free-tier defaults from the models a key can use.

    Both default to the newest stable Flash-Lite, which has the most free requests per day.
    'main' can be changed to a Flash model in setup for better answers but fewer free requests.
    """
    def version(name):
        m = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
        return float(m.group(1)) if m else 0.0

    skip = ("image", "tts", "audio", "live", "embedding", "vision", "thinking", "learnlm", "robotics")
    usable = [m for m in model_ids if m.startswith("gemini-") and not any(s in m for s in skip)]
    # newest first; within one version, stable names (shorter, no 'preview'/'exp') first
    usable.sort(key=lambda n: (-version(n), "preview" in n or "exp" in n, len(n)))
    lite = [m for m in usable if "flash-lite" in m]
    flash = [m for m in usable if "flash" in m and "flash-lite" not in m]
    cheap = lite[0] if lite else (flash[0] if flash else "")
    return {"main": cheap, "cheap": cheap, "flash": flash[0] if flash else ""}


class OpenAICompatibleProvider(Provider):
    def __init__(self, api_key: str, base_url: str, name: str = "gemini", reasoning_effort: str = "",
                 transport=None, timeout: float = 60.0):
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.name = name
        self.reasoning_effort = reasoning_effort
        self._transport = transport or _default_transport
        self.timeout = timeout

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._api_key}", "content-type": "application/json"}

    def _clean(self, text: str) -> str:
        return text.replace(self._api_key, "[hidden]") if self._api_key else text

    def _need_key(self):
        if not self._api_key:
            raise ProviderError("There is no API key yet. Paste one in Settings (or run 'python -m src setup').")

    def complete(self, *, model, system, messages, tools=None, max_tokens=1024) -> Response:
        self._need_key()
        payload = {"model": model, "max_tokens": max_tokens, "messages": to_chat_messages(system, messages)}
        if tools:
            payload["tools"] = to_chat_tools(tools)
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        status, raw = self._transport("POST", self.base_url + "/chat/completions", self._headers(),
                                      json.dumps(payload).encode("utf-8"), self.timeout)
        if status != 200:
            raise ProviderError(self._friendly_error(status, raw))
        return self._parse(raw, model)

    def list_models(self) -> list:
        """Model names this key can use, e.g. ['gemini-x-flash-lite', ...]."""
        self._need_key()
        status, raw = self._transport("GET", self.base_url + "/models", self._headers(), None, self.timeout)
        if status != 200:
            raise ProviderError(self._friendly_error(status, raw))
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise ProviderError("The model service sent a reply that could not be read. Try again.")
        return [str(m.get("id", "")).removeprefix("models/") for m in data.get("data", []) if m.get("id")]

    def _friendly_error(self, status: int, raw: bytes) -> str:
        detail = ""
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
            if isinstance(data, list) and data:
                data = data[0]
            detail = str(data.get("error", {}).get("message", ""))
        except (ValueError, AttributeError):
            pass
        low = detail.lower()
        if status in (401, 403) or "api key" in low:
            if "location" in low or "region" in low:
                msg = "This service does not offer its free API in your country or region."
            else:
                msg = "The API key was rejected. Paste a new key in Settings (or run 'python -m src setup')."
        elif status == 404:
            msg = "The model name was not found. Pick another model in Settings (or run 'python -m src setup')."
        elif status == 429:
            msg = ("You reached the free usage limit for now. Wait a minute and try again. "
                   "If it keeps happening, the daily free limit is used up and resets at midnight Pacific time.")
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
            choice = data["choices"][0]
            message = choice.get("message") or {}
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderError("The model service sent a reply that could not be read. Try again.")
        text = message.get("content") or ""
        content = [{"type": "text", "text": text}] if text else []
        tool_calls = []
        bad_tool_args = False
        for i, call in enumerate(message.get("tool_calls") or []):
            fn = call.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
                if not isinstance(args, dict):
                    raise ValueError("tool arguments must be an object")
            except ValueError:
                bad_tool_args = True
                continue
            tc = ToolCall(id=call.get("id") or f"call_{i}", name=fn.get("name", ""), input=args)
            tool_calls.append(tc)
            block = {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input}
            if call.get("extra_content"):
                block["extra_content"] = call["extra_content"]
            content.append(block)
        finish = choice.get("finish_reason", "")
        truncated = bad_tool_args or (finish == "length" and not text and not tool_calls)
        if truncated:
            # Never run a half-finished tool call or leave a broken tool_use block in the chat history.
            tool_calls = []
            content = [{"type": "text", "text": text}] if text else []
        reasoning = str(message.get("reasoning_content") or message.get("reasoning") or "").strip()
        usage = data.get("usage") or {}
        cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
        return Response(
            text=text,
            content=content,
            tool_calls=tool_calls,
            input_tokens=max(0, int(usage.get("prompt_tokens", 0) or 0) - cached),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            cache_read_tokens=cached,
            model=data.get("model") or model,
            stop_reason=finish,
            truncated=truncated,
            reasoning=reasoning,
        )
