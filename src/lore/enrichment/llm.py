"""Lightweight LLM client abstraction using litellm."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_LITELLM_IMPORT_ERROR = (
    "Enrichment requires the 'litellm' package. "
    "Install with: pip install lore-memory[enrichment]"
)


class LLMClient:
    """Thin wrapper for LLM completion calls.

    Uses litellm for provider-agnostic access to OpenAI, Anthropic,
    and Google models.
    """

    def __init__(self, model: str, provider: Optional[str] = None) -> None:
        try:
            import litellm  # noqa: F401
        except ImportError:
            raise ImportError(_LITELLM_IMPORT_ERROR)

        self.model = model
        self.provider = provider or self._detect_provider(model)
        self._warned_no_key = False

    def complete(self, prompt: str, response_format: Optional[Dict[str, Any]] = None) -> str:
        """Send prompt to LLM, return response text.

        Raises on network/API errors -- caller must handle.
        """
        import litellm

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
        }
        if response_format:
            kwargs["response_format"] = response_format

        response = litellm.completion(**kwargs)
        return response.choices[0].message.content

    def check_api_key(self) -> bool:
        """Check if the required API key is available.

        Returns True if key is present, False otherwise.
        Logs a warning once if key is missing.
        """
        key_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google": "GOOGLE_API_KEY",
        }
        env_var = key_map.get(self.provider)
        if env_var and not os.environ.get(env_var):
            if not self._warned_no_key:
                logger.warning(
                    "Enrichment skipped: %s not set for provider '%s'",
                    env_var,
                    self.provider,
                )
                self._warned_no_key = True
            return False
        return True

    @staticmethod
    def _detect_provider(model: str) -> str:
        """Auto-detect provider from model name."""
        if model.startswith(("gpt-", "o1", "o3", "o4")):
            return "openai"
        if model.startswith(("claude-",)):
            return "anthropic"
        if model.startswith(("gemini-",)):
            return "google"
        # Fallback: let litellm figure it out
        return "openai"
