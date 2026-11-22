"""Setup rules shared by the terminal setup (cli.py) and the window (ui/controller.py).

Keeping them in one place means both ways of setting up Pilot check model names, pick free models and
record prices in exactly the same way.
"""
from __future__ import annotations

import re

from .providers import PROVIDER_INFO, ProviderError
from .providers.openai_compatible import pick_models

FREE_PRICE = {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0}
MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
KNOWN_PREFIXES = tuple(info["model_prefix"] for info in PROVIDER_INFO.values())


def model_prefix(provider: str) -> str:
    return PROVIDER_INFO[provider]["model_prefix"]


def valid_model(name: str, provider: str, secrets: list) -> bool:
    """A model name for this provider: no spaces, right prefix, and never a pasted API key."""
    return bool(MODEL_NAME.match(name or "")) and name.startswith(model_prefix(provider)) and name not in secrets


def looks_like_key(name: str, provider: str, secrets: list) -> bool:
    return name in secrets or (len(name) > 30 and not name.startswith(model_prefix(provider)))


def reset_models_if_invalid(settings, provider: str) -> None:
    secrets = settings.secrets()
    if not all(valid_model(str(settings.get("models", k, default="")), provider, secrets) for k in ("main", "cheap")):
        settings.set("models", dict(PROVIDER_INFO[provider]["models"]))


def suggest_models(settings, provider_factory) -> dict:
    """For Gemini, ask the service which models this key can use. Returns {} when not possible.

    Uses a model-list request, which costs no tokens.
    """
    if settings.provider != "gemini" or not settings.api_key():
        return {}
    ids = provider_factory(settings).list_models()  # may raise ProviderError; callers show it
    picked = pick_models(ids)
    picked["all"] = [m for m in ids if valid_model(m, "gemini", [])]
    return picked if picked["main"] else {}


def finish_models(settings) -> None:
    """After models are chosen: free-tier price 0 for Gemini, and drop junk price entries."""
    if settings.provider == "gemini":
        for model in (settings.get("models", "main"), settings.get("models", "cheap")):
            settings.set("prices_per_million_tokens", model, dict(FREE_PRICE))
    clean_prices(settings)


def clean_prices(settings) -> None:
    """Drop price entries that are not model names (left over from an earlier mistyped setup)."""
    prices = settings.get("prices_per_million_tokens", default={}) or {}
    secrets = settings.secrets()
    for name in list(prices):
        if not (MODEL_NAME.match(name) and name.startswith(KNOWN_PREFIXES)) or name in secrets:
            del prices[name]


def check_connection(settings, provider_factory) -> str:
    """One tiny call to prove the key works. Returns the model's short answer. Raises ProviderError."""
    reply = provider_factory(settings).complete(
        model=settings.get("models", "cheap"), system="", max_tokens=256,
        messages=[{"role": "user", "content": "Say OK."}])
    return reply.text.strip()[:40] or "(empty reply)"


__all__ = ["FREE_PRICE", "MODEL_NAME", "valid_model", "looks_like_key", "reset_models_if_invalid",
           "suggest_models", "finish_models", "clean_prices", "check_connection", "ProviderError"]
