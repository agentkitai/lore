"""Tests for LLMProvider factory and OpenAIProvider with mocked HTTP."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lore.llm import LLMProvider, OpenAIProvider, create_provider
from lore.llm.base import LLMProvider as BaseLLMProvider

# ── Factory tests ───────────────────────────────────────────────────


class TestCreateProvider:
    def test_openai_provider(self):
        provider = create_provider(
            provider="openai",
            model="gpt-4o-mini",
            api_key="sk-test-key",
        )
        assert isinstance(provider, OpenAIProvider)

    def test_openai_with_custom_base_url(self):
        provider = create_provider(
            provider="openai",
            model="gpt-4o-mini",
            api_key="sk-test-key",
            base_url="http://localhost:8080/v1",
        )
        assert isinstance(provider, OpenAIProvider)
        assert provider._base_url == "http://localhost:8080/v1"

    def test_no_api_key_raises(self):
        with pytest.raises(ValueError, match="api_key required"):
            create_provider(provider="openai", api_key=None)

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            create_provider(provider="gemini", api_key="key")

    def test_unknown_provider_lists_supported(self):
        with pytest.raises(ValueError, match="openai"):
            create_provider(provider="gemini", api_key="key")

    def test_default_model(self):
        provider = create_provider(api_key="sk-test")
        assert provider._model == "gpt-4o-mini"

    def test_default_base_url(self):
        provider = create_provider(api_key="sk-test")
        assert provider._base_url == "https://api.openai.com/v1"


# ── OpenAIProvider tests (mocked HTTP) ──────────────────────────────


class TestOpenAIProvider:
    def test_complete_sends_correct_request(self):
        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o-mini")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hello response"}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("lore.llm.openai.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = provider.complete("Hello", max_tokens=50)
            assert result == "Hello response"

            # Verify correct URL
            call_args = mock_client.post.call_args
            assert call_args[0][0] == "https://api.openai.com/v1/chat/completions"

            # Verify auth header
            headers = call_args[1]["headers"]
            assert headers["Authorization"] == "Bearer sk-test"

            # Verify request body
            body = call_args[1]["json"]
            assert body["model"] == "gpt-4o-mini"
            assert body["max_tokens"] == 50
            assert body["temperature"] == 0.1
            assert body["messages"][0]["content"] == "Hello"

    def test_custom_base_url(self):
        provider = OpenAIProvider(
            api_key="sk-test",
            model="local-model",
            base_url="http://localhost:8080/v1/",
        )
        assert provider._base_url == "http://localhost:8080/v1"

    def test_is_llm_provider(self):
        provider = OpenAIProvider(api_key="sk-test")
        assert isinstance(provider, LLMProvider)


# ── LLMProvider ABC test ────────────────────────────────────────────


class TestLLMProviderABC:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            BaseLLMProvider()  # type: ignore[abstract]
