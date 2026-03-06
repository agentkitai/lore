"""Abstract LLM provider — shared between F6 and F9."""

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Abstract LLM provider — shared between F6 and F9."""

    @abstractmethod
    def complete(self, prompt: str, *, max_tokens: int = 200) -> str:
        """Send a prompt and return the response text."""
        ...
