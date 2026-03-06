"""Shared LLM provider module — used by F9 (classification) and F6 (enrichment)."""

from __future__ import annotations

from typing import Optional

from lore.llm.base import LLMProvider
from lore.llm.openai import OpenAIProvider

_SUPPORTED_PROVIDERS = ("openai",)


def create_provider(
    provider: str = "openai",
    model: str = "gpt-4o-mini",
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> LLMProvider:
    """Create an LLM provider from config."""
    if provider == "openai":
        if not api_key:
            raise ValueError("api_key required for OpenAI provider")
        return OpenAIProvider(
            api_key=api_key,
            model=model,
            base_url=base_url or "https://api.openai.com/v1",
        )
    raise ValueError(
        f"Unknown LLM provider: {provider!r}. "
        f"Supported providers: {', '.join(_SUPPORTED_PROVIDERS)}"
    )


__all__ = ["LLMProvider", "OpenAIProvider", "create_provider"]
