"""Settings live in one JSON file: data/settings.json.

API keys are stored only here, the file is listed in .gitignore, and nothing in
this app ever prints a key. Use Settings.redact() on any text that might contain one.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

APP_NAME = "Pilot"

DEFAULTS = {
    "provider": "gemini",  # free tier, no card. Change to "anthropic" to use paid Claude models.
    "api_keys": {"gemini": "", "anthropic": ""},
    "models": {"main": "gemini-flash-lite-latest", "cheap": "gemini-flash-lite-latest"},
    "reasoning_effort": "low",  # Gemini only: keeps hidden "thinking" short, so replies are faster and use less quota
    "limits": {
        "daily_tokens": 300000,
        "daily_usd": 2.0,
        "max_calls_per_task": 8,
        "max_output_tokens": 1024,
        "max_output_tokens_with_tools": 4096,
        "max_tool_output_chars": 4000,
    },
    "context": {"recent_turns": 6, "max_summary_chars": 1200},
    "prompt_caching": True,
    "enabled_tools": [],
    "ui": {"theme": "dark"},  # "dark" or "light"
    # One entry per connector: on/off, whether its test passed, and what the test learned (no secrets here).
    "connectors": {},
    # Connector secrets (like the Telegram bot token). Hidden in all printed text, same as API keys.
    "connector_secrets": {},
    "prices_per_million_tokens": {
        "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.10, "cache_write": 2.5},
        "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": None},
        "gemini-flash-lite-latest": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
    },
    "prices_note": (
        "USD per million tokens. Claude prices are from public price lists in October 2026; Haiku 5.5 prices "
        "apply to prompts up to 100,000 tokens. Gemini models are set to 0 because Pilot uses the free tier "
        "(setup adds them). If you turn on billing in Google AI Studio, put real prices here."
    ),
}


class SettingsError(Exception):
    """A problem with the settings file, worded for a non-technical reader."""


def project_root() -> Path:
    env = os.environ.get("PILOT_HOME")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    return project_root() / "data"


def settings_path() -> Path:
    return data_dir() / "settings.json"


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Settings:
    def __init__(self, values: dict | None = None, path: Path | str | None = None):
        self.values = deep_merge(DEFAULTS, values or {})
        self.path = Path(path) if path else None

    @classmethod
    def load(cls, path: Path | str | None = None) -> "Settings":
        path = Path(path) if path else settings_path()
        values: dict = {}
        if path.exists():
            try:
                values = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SettingsError(
                    f"Could not read {path.name} ({exc}). Fix the file, or delete it and run setup again."
                )
            if not isinstance(values, dict):
                raise SettingsError(f"{path.name} should hold a settings object. Delete it and run setup again.")
        return cls(values, path)

    def save(self) -> None:
        if self.path is None:
            raise SettingsError("These settings have no file to save to.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.values, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)  # ignored on Windows
        except OSError:
            pass
        os.replace(tmp, self.path)

    def get(self, *keys, default=None):
        node = self.values
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def set(self, *keys_and_value) -> None:
        *keys, value = keys_and_value
        node = self.values
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = value

    @property
    def provider(self) -> str:
        return self.get("provider", default="anthropic")

    def api_key(self, provider: str | None = None) -> str:
        return (self.get("api_keys", provider or self.provider, default="") or "").strip()

    def set_api_key(self, provider: str, key: str) -> None:
        self.set("api_keys", provider, key.strip())

    def secrets(self) -> list:
        values = list((self.get("api_keys", default={}) or {}).values())
        for group in (self.get("connector_secrets", default={}) or {}).values():
            if isinstance(group, dict):
                values += list(group.values())
        return [v for v in values if isinstance(v, str) and len(v) >= 8]

    def redact(self, text: str) -> str:
        for secret in self.secrets():
            text = text.replace(secret, "[hidden]")
        return text

    def __repr__(self) -> str:  # never show keys
        return f"Settings(provider={self.provider!r}, path={str(self.path)!r})"
