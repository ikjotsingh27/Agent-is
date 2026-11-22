"""Pick a provider by name. Switching providers is one setting in data/settings.json."""
from __future__ import annotations

from .anthropic_provider import AnthropicProvider
from .base import Provider, ProviderError, Response, ToolCall
from .mock_provider import MockProvider
from .openai_compatible import GEMINI_BASE_URL, OpenAICompatibleProvider

PROVIDERS = ("gemini", "anthropic", "mock")

# What setup suggests for each provider. Gemini's names are replaced at setup by the newest models
# the key can actually use, so these are only a fallback.
PROVIDER_INFO = {
    "gemini": {
        "label": "Google Gemini (free, no card needed)",
        "models": {"main": "gemini-flash-lite-latest", "cheap": "gemini-flash-lite-latest"},
        "model_prefix": "gemini",
        "key_help": "Get a free key at aistudio.google.com: sign in with Google, click 'Get API key', "
                    "then 'Create API key'.",
    },
    "anthropic": {
        "label": "Anthropic Claude (paid)",
        "models": {"main": "claude-sonnet-5-5", "cheap": "claude-haiku-5-5"},
        "model_prefix": "claude",
        "key_help": "Get a key at console.anthropic.com (needs billing credit).",
    },
    "mock": {"label": "Mock (fake replies for testing)", "models": {"main": "mock", "cheap": "mock"},
             "model_prefix": "mock", "key_help": ""},
}


def get_provider(settings) -> Provider:
    name = settings.provider
    if name == "anthropic":
        return AnthropicProvider(
            api_key=settings.api_key("anthropic"),
            prompt_caching=bool(settings.get("prompt_caching", default=True)),
        )
    if name == "gemini":
        # Gemini caches repeated prompt starts automatically, so there is no caching switch to send.
        return OpenAICompatibleProvider(
            api_key=settings.api_key("gemini"), base_url=GEMINI_BASE_URL, name="gemini",
            reasoning_effort=settings.get("reasoning_effort", default="low") or "",
        )
    if name == "mock":
        return MockProvider()
    raise ProviderError(f"Unknown provider '{name}'. Choose one of: {', '.join(PROVIDERS)}.")


__all__ = ["get_provider", "PROVIDERS", "PROVIDER_INFO", "Provider", "ProviderError", "Response", "ToolCall",
           "AnthropicProvider", "MockProvider", "OpenAICompatibleProvider"]
