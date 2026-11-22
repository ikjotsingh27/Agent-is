"""Cheap-model routing for easy jobs.

From the blueprint's efficiency rules:
- Simple tasks (short greetings, formatting, rephrasing, quick single-fact questions that need no tools or
  files) go to the smaller, cheaper model (models.cheap).
- Tasks that use tools, inspect files, write documents/code, browse the web, or need multi-step reasoning go
  to models.main.
"""
from __future__ import annotations

import re

_HEAVY_WORDS = re.compile(
    r"\b(file|files|folder|workspace|pdf|doc|docx|xlsx|pptx|csv|image|photo|screenshot|attach|audio|voice|"
    r"command|powershell|terminal|run|script|python|code|program|install|delete|remove|tidy|"
    r"search|web|browse|page|site|url|http|https|news|weather|price|stock|"
    r"email|emails|mail|gmail|inbox|calendar|event|events|meeting|meetings|schedule|appointment|appointments|"
    r"send|remind|reminder|remember|recall|forget|memory|note|notes|plan|analyze|compare|debug|build|create|make|write)\b",
    re.I,
)


def pick_model(text: str, settings, has_loaded_tools: bool = False) -> str:
    """Choose models.cheap for short, tool-free conversational turns, and models.main for everything else."""
    main = settings.get("models", "main") or ""
    cheap = settings.get("models", "cheap") or main
    if not cheap or cheap == main or has_loaded_tools:
        return main
    stripped = (text or "").strip()
    if not stripped or len(stripped) > 140 or "\n" in stripped:
        return main
    if "[Files the user sent you" in stripped or _HEAVY_WORDS.search(stripped):
        return main
    return cheap
