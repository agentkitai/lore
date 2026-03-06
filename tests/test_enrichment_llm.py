"""Unit tests for the LLMClient abstraction."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Ensure API keys are not set unless explicitly provided."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)


class TestDetectProvider:
    def test_openai_gpt(self):
        from lore.enrichment.llm import LLMClient
        assert LLMClient._detect_provider("gpt-4o-mini") == "openai"

    def test_openai_o1(self):
        from lore.enrichment.llm import LLMClient
        assert LLMClient._detect_provider("o1-preview") == "openai"

    def test_openai_o4(self):
        from lore.enrichment.llm import LLMClient
        assert LLMClient._detect_provider("o4-mini") == "openai"

    def test_anthropic(self):
        from lore.enrichment.llm import LLMClient
        assert LLMClient._detect_provider("claude-3-haiku") == "anthropic"

    def test_google(self):
        from lore.enrichment.llm import LLMClient
        assert LLMClient._detect_provider("gemini-pro") == "google"

    def test_unknown_fallback(self):
        from lore.enrichment.llm import LLMClient
        assert LLMClient._detect_provider("unknown-model") == "openai"


@patch("lore.enrichment.llm.litellm", create=True)
class TestLLMClient:
    def _make_client(self, model="gpt-4o-mini", provider=None):
        # Patch the import inside __init__
        import sys
        mock_litellm = MagicMock()
        sys.modules["litellm"] = mock_litellm

        from lore.enrichment.llm import LLMClient
        client = LLMClient(model=model, provider=provider)

        del sys.modules["litellm"]
        return client, mock_litellm

    def test_check_api_key_present(self, mock_litellm, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        client, _ = self._make_client()
        assert client.check_api_key() is True

    def test_check_api_key_missing(self, mock_litellm, caplog):
        client, _ = self._make_client()
        with caplog.at_level(logging.WARNING):
            result = client.check_api_key()
        assert result is False
        assert "OPENAI_API_KEY" in caplog.text

    def test_check_api_key_warn_once(self, mock_litellm, caplog):
        client, _ = self._make_client()
        with caplog.at_level(logging.WARNING):
            client.check_api_key()
            client.check_api_key()
        # Warning should appear only once
        assert caplog.text.count("OPENAI_API_KEY") == 1

    def test_complete_calls_litellm(self, mock_litellm, monkeypatch):
        import sys
        sys.modules["litellm"] = mock_litellm

        mock_response = MagicMock()
        mock_response.choices[0].message.content = '{"topics": []}'
        mock_litellm.completion.return_value = mock_response

        from lore.enrichment.llm import LLMClient
        client = LLMClient(model="gpt-4o-mini")
        result = client.complete("test prompt")

        mock_litellm.completion.assert_called_once()
        call_kwargs = mock_litellm.completion.call_args
        assert call_kwargs.kwargs["model"] == "gpt-4o-mini"
        assert call_kwargs.kwargs["temperature"] == 0.0
        assert result == '{"topics": []}'

        del sys.modules["litellm"]

    def test_provider_auto_detect(self, mock_litellm):
        client, _ = self._make_client("claude-3-haiku")
        assert client.provider == "anthropic"

    def test_provider_explicit(self, mock_litellm):
        client, _ = self._make_client("my-custom-model", provider="google")
        assert client.provider == "google"


def test_import_error_no_litellm():
    """Verify ImportError when litellm is not installed."""
    import sys
    # Temporarily remove litellm from modules
    saved = sys.modules.pop("litellm", None)
    # Also block future imports
    import importlib
    orig_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

    def mock_import(name, *args, **kwargs):
        if name == "litellm":
            raise ImportError("No module named 'litellm'")
        return orig_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        # Need to reload the module to test the import path
        from lore.enrichment import llm
        importlib.reload(llm)
        with pytest.raises(ImportError, match="pip install lore-memory"):
            llm.LLMClient(model="gpt-4o-mini")

    # Restore
    if saved is not None:
        sys.modules["litellm"] = saved
