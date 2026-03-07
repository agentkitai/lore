"""Tests for the OpenClaw-Lore bridge."""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

# Import from tools directory
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from importlib import import_module

# Import the bridge module dynamically (filename has hyphens)
import importlib.util
_bridge_path = os.path.join(
    os.path.dirname(__file__), "..", "tools", "openclaw-lore-bridge.py"
)
_spec = importlib.util.spec_from_file_location("openclaw_lore_bridge", _bridge_path)
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)


class TestParseLogEntry:
    def test_conversation_type(self):
        entry = {
            "type": "conversation",
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
            ],
        }
        result = bridge._parse_log_entry(entry)
        assert result is not None
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"

    def test_turn_type(self):
        entry = {"type": "turn", "role": "assistant", "content": "response text"}
        result = bridge._parse_log_entry(entry)
        assert result is not None
        assert len(result) == 1
        assert result[0]["content"] == "response text"

    def test_response_event(self):
        entry = {"event": "response", "input": "question", "output": "answer"}
        result = bridge._parse_log_entry(entry)
        assert result is not None
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"

    def test_unknown_type_returns_none(self):
        entry = {"type": "debug", "data": "some debug info"}
        assert bridge._parse_log_entry(entry) is None

    def test_empty_messages_returns_none(self):
        entry = {"type": "conversation", "messages": []}
        assert bridge._parse_log_entry(entry) is None

    def test_turn_without_content_returns_none(self):
        entry = {"type": "turn", "role": "user"}
        assert bridge._parse_log_entry(entry) is None


class TestProcessLogFile:
    def test_process_new_entries(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(json.dumps({
                "type": "conversation",
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello"},
                ],
            }) + "\n")
            log_path = f.name

        try:
            state = {}
            with patch.object(bridge, "_send_conversation", return_value=True) as mock_send:
                sent = bridge._process_log_file(
                    log_path, state,
                    api_url="http://localhost:8000",
                    api_key="test-key",
                )

            assert sent == 1
            mock_send.assert_called_once()
            assert state[log_path] > 0
        finally:
            os.unlink(log_path)

    def test_dedup_skips_already_processed(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            content = json.dumps({
                "type": "conversation",
                "messages": [{"role": "user", "content": "hi"}],
            }) + "\n"
            f.write(content)
            log_path = f.name

        try:
            # Set state to end of file
            state = {log_path: len(content.encode())}
            with patch.object(bridge, "_send_conversation", return_value=True) as mock_send:
                sent = bridge._process_log_file(
                    log_path, state,
                    api_url="http://localhost:8000",
                    api_key="test-key",
                )

            assert sent == 0
            mock_send.assert_not_called()
        finally:
            os.unlink(log_path)

    def test_process_turn_entries(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(json.dumps({"type": "turn", "role": "user", "content": "q1"}) + "\n")
            f.write(json.dumps({"type": "turn", "role": "assistant", "content": "a1"}) + "\n")
            log_path = f.name

        try:
            state = {}
            with patch.object(bridge, "_send_conversation", return_value=True) as mock_send:
                sent = bridge._process_log_file(
                    log_path, state,
                    api_url="http://localhost:8000",
                    api_key="test-key",
                )

            # Turns get batched and sent
            assert sent == 1
            call_messages = mock_send.call_args[0][0]
            assert len(call_messages) == 2
        finally:
            os.unlink(log_path)

    def test_malformed_json_skipped(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("not json\n")
            f.write(json.dumps({
                "type": "conversation",
                "messages": [{"role": "user", "content": "valid"}],
            }) + "\n")
            log_path = f.name

        try:
            state = {}
            with patch.object(bridge, "_send_conversation", return_value=True) as mock_send:
                sent = bridge._process_log_file(
                    log_path, state,
                    api_url="http://localhost:8000",
                    api_key="test-key",
                )

            assert sent == 1
        finally:
            os.unlink(log_path)


class TestFindLogFiles:
    def test_finds_matching_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create matching and non-matching files
            open(os.path.join(tmpdir, "openclaw-2026-03-07.log"), "w").close()
            open(os.path.join(tmpdir, "openclaw-2026-03-06.log"), "w").close()
            open(os.path.join(tmpdir, "other.log"), "w").close()

            files = bridge._find_log_files(tmpdir)
            assert len(files) == 2
            assert all("openclaw-" in f for f in files)


class TestStateManagement:
    def test_save_and_load_state(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_path = f.name

        try:
            with patch.object(bridge, "STATE_FILE", state_path):
                bridge._save_state({"test.log": 42})
                loaded = bridge._load_state()
                assert loaded == {"test.log": 42}
        finally:
            os.unlink(state_path)

    def test_load_missing_state(self):
        with patch.object(bridge, "STATE_FILE", "/tmp/__nonexistent_state_12345__.json"):
            state = bridge._load_state()
            assert state == {}


class TestRunBridge:
    def test_once_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a log file
            log_path = os.path.join(tmpdir, "openclaw-2026-03-07.log")
            with open(log_path, "w") as f:
                f.write(json.dumps({
                    "type": "conversation",
                    "messages": [
                        {"role": "user", "content": "test"},
                        {"role": "assistant", "content": "response"},
                    ],
                }) + "\n")

            with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as sf:
                state_path = sf.name

            try:
                with patch.object(bridge, "STATE_FILE", state_path):
                    with patch.object(bridge, "_send_conversation", return_value=True) as mock_send:
                        bridge.run_bridge(
                            api_url="http://localhost:8000",
                            api_key="test-key",
                            log_dir=tmpdir,
                            once=True,
                        )

                mock_send.assert_called_once()
            finally:
                os.unlink(state_path)


class TestSendConversation:
    def test_success(self):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"job_id": "j1", "message_count": 1}
        mock_client.post.return_value = mock_resp

        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)

            result = bridge._send_conversation(
                [{"role": "user", "content": "hi"}],
                api_url="http://localhost:8000",
                api_key="key",
            )
        assert result is True

    def test_failure_returns_false(self):
        with patch("httpx.Client", side_effect=Exception("connection refused")):
            result = bridge._send_conversation(
                [{"role": "user", "content": "hi"}],
                api_url="http://localhost:8000",
                api_key="key",
            )
        assert result is False
