"""OpenAI-compatible API provider."""

import httpx

from lore.llm.base import LLMProvider


class OpenAIProvider(LLMProvider):
    """OpenAI-compatible API provider (works with OpenAI, local models, proxies)."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
    ):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")

    def complete(self, prompt: str, *, max_tokens: int = 200) -> str:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": 0.1,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
