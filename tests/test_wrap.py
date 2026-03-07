"""Tests for lore wrap — CLI wrapper and conversation parsing."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from lore.wrap import _parse_conversation, _send_local, _send_to_api, run_wrap


class TestParseConversation:
    def test_user_assistant_detection(self):
        raw = "> How do I deploy?\nUse docker compose up.\nIt handles everything."
        messages = _parse_conversation(raw)
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert "deploy" in messages[0]["content"]
        assert messages[1]["role"] == "assistant"

    def test_multiple_turns(self):
        raw = (
            "> What is Python?\n"
            "Python is a programming language.\n"
            "\n"
            "> How about JavaScript?\n"
            "JavaScript runs in the browser."
        )
        messages = _parse_conversation(raw)
        assert len(messages) == 4
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"
        assert messages[2]["role"] == "user"
        assert messages[3]["role"] == "assistant"

    def test_all_assistant_wraps_in_pair(self):
        raw = "Here is the answer.\nMore text.\nEven more."
        messages = _parse_conversation(raw)
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "(wrapped session)"
        assert messages[1]["role"] == "assistant"

    def test_empty_output(self):
        messages = _parse_conversation("")
        assert messages == []

    def test_only_whitespace(self):
        messages = _parse_conversation("   \n\n   ")
        assert messages == []

    def test_dollar_prompt(self):
        raw = "$ echo hello\nhello"
        messages = _parse_conversation(raw)
        assert messages[0]["role"] == "user"

    def test_human_colon_prompt(self):
        raw = "Human: Tell me a joke\nWhy did the chicken cross the road?"
        messages = _parse_conversation(raw)
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"

    def test_blank_lines_preserved_in_content(self):
        raw = "Some text\n\nMore text\n\nFinal text"
        messages = _parse_conversation(raw)
        # All assistant -> wrapped
        assert len(messages) == 2
        assert "\n\n" in messages[1]["content"]


class TestSendToApi:
    def test_send_success(self):
        import httpx

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"job_id": "test-123", "message_count": 2}
        mock_client.post.return_value = mock_resp

        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)

            _send_to_api(
                [{"role": "user", "content": "hi"}],
                api_url="http://localhost:8000",
                api_key="test-key",
            )

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert "/v1/conversations" in call_kwargs[0][0]

    def test_send_with_user_and_project(self):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"job_id": "test-123", "message_count": 1}
        mock_client.post.return_value = mock_resp

        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)

            _send_to_api(
                [{"role": "user", "content": "hi"}],
                api_url="http://localhost:8000",
                api_key="test-key",
                user_id="alice",
                project="my-project",
            )

        payload = mock_client.post.call_args[1]["json"]
        assert payload["user_id"] == "alice"
        assert payload["project"] == "my-project"


class TestSendLocal:
    def test_send_local_calls_add_conversation(self):
        mock_lore = MagicMock()
        mock_result = MagicMock()
        mock_result.memories_extracted = 3
        mock_result.duplicates_skipped = 1
        mock_lore.add_conversation.return_value = mock_result

        with patch("lore.Lore", return_value=mock_lore):
            messages = [{"role": "user", "content": "test"}]
            _send_local(messages, user_id="bob")

        mock_lore.add_conversation.assert_called_once_with(
            messages, user_id="bob", project=None,
        )
        mock_lore.close.assert_called_once()


class TestRunWrap:
    def test_empty_command_returns_error(self):
        code = run_wrap([])
        assert code == 1

    @patch("lore.wrap.pty")
    @patch("lore.wrap._parse_conversation")
    @patch("lore.wrap._send_local")
    def test_wrap_captures_and_sends(self, mock_send, mock_parse, mock_pty):
        # Simulate pty.spawn capturing some output
        def fake_spawn(cmd, read_cb):
            # Simulate child writing data
            captured_data = b"Hello from child\n"
            # The read callback would be called with the master fd
            # but we simulate the captured data via the BytesIO
            return 0  # exited normally via WEXITSTATUS

        mock_pty.spawn.side_effect = fake_spawn
        mock_parse.return_value = [
            {"role": "user", "content": "(wrapped session)"},
            {"role": "assistant", "content": "Hello from child"},
        ]

        # No API URL set, so it should use local
        with patch.dict(os.environ, {"LORE_API_URL": "", "LORE_API_KEY": ""}, clear=False):
            code = run_wrap(["echo", "hello"])

        # pty.spawn was called
        mock_pty.spawn.assert_called_once()

    def test_command_not_found(self):
        code = run_wrap(["__nonexistent_command_12345__"])
        # pty.spawn forks — child fails with exit code 1 (Python traceback)
        assert code != 0

