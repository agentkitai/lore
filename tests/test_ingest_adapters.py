"""Tests for source adapters (F7-S1 through S4)."""

import hashlib
import hmac
import time

import pytest

from lore.ingest.adapters import get_adapter
from lore.ingest.adapters.raw import RawAdapter


class TestAdapterRegistry:
    def test_get_raw_adapter(self):
        adapter = get_adapter("raw")
        assert isinstance(adapter, RawAdapter)

    def test_unknown_adapter_raises(self):
        with pytest.raises(ValueError, match="Unknown source adapter: unknown"):
            get_adapter("unknown")

    def test_get_slack_adapter(self):
        adapter = get_adapter("slack", signing_secret="test")
        assert adapter.adapter_name == "slack"

    def test_get_telegram_adapter(self):
        adapter = get_adapter("telegram", bot_token="test-token")
        assert adapter.adapter_name == "telegram"

    def test_get_git_adapter(self):
        adapter = get_adapter("git")
        assert adapter.adapter_name == "git"


class TestRawAdapter:
    def test_normalize_basic(self):
        adapter = RawAdapter()
        payload = {
            "content": "Some text",
            "user": "alice",
            "channel": "manual",
            "type": "lesson",
            "tags": ["important"],
        }
        msg = adapter.normalize(payload)
        assert msg.content == "Some text"
        assert msg.user == "alice"
        assert msg.channel == "manual"
        assert msg.memory_type == "lesson"
        assert msg.tags == ["important"]
        assert msg.raw_format == "plain_text"

    def test_normalize_defaults(self):
        adapter = RawAdapter()
        msg = adapter.normalize({"content": "hello"})
        assert msg.memory_type == "general"
        assert msg.user is None
        assert msg.tags is None

    def test_verify_always_true(self):
        adapter = RawAdapter()
        assert adapter.verify({}, b"") is True


class TestSlackAdapter:
    def _make_adapter(self, secret="test-secret"):
        return get_adapter("slack", signing_secret=secret)

    def test_normalize_payload(self):
        adapter = self._make_adapter()
        payload = {
            "event": {
                "text": "*bold* text",
                "user": "U123",
                "channel": "C456",
                "ts": "1709734200.123456",
            }
        }
        msg = adapter.normalize(payload)
        assert msg.content == "bold text"
        assert msg.user == "U123"
        assert msg.channel == "C456"
        assert msg.timestamp == "1709734200.123456"
        assert msg.source_message_id == "1709734200.123456"
        assert msg.raw_format == "slack_mrkdwn"

    def test_verify_valid_signature(self):
        secret = "test-secret"
        adapter = self._make_adapter(secret)
        body = b'{"event":{"text":"hi"}}'
        ts = str(int(time.time()))
        sig_basestring = f"v0:{ts}:{body.decode('utf-8')}"
        expected = "v0=" + hmac.new(
            secret.encode(), sig_basestring.encode(), hashlib.sha256
        ).hexdigest()
        headers = {
            "x-slack-request-timestamp": ts,
            "x-slack-signature": expected,
        }
        assert adapter.verify(headers, body) is True

    def test_verify_invalid_signature(self):
        adapter = self._make_adapter()
        headers = {
            "x-slack-request-timestamp": str(int(time.time())),
            "x-slack-signature": "v0=invalid",
        }
        assert adapter.verify(headers, b"body") is False

    def test_verify_replay_attack(self):
        adapter = self._make_adapter()
        old_ts = str(int(time.time()) - 600)  # 10 minutes ago
        headers = {
            "x-slack-request-timestamp": old_ts,
            "x-slack-signature": "v0=anything",
        }
        assert adapter.verify(headers, b"body") is False

    def test_is_url_verification(self):
        from lore.ingest.adapters.slack import SlackAdapter

        assert SlackAdapter.is_url_verification({"type": "url_verification", "challenge": "abc"})
        assert not SlackAdapter.is_url_verification({"type": "event_callback"})

    def test_is_bot_message(self):
        from lore.ingest.adapters.slack import SlackAdapter

        assert SlackAdapter.is_bot_message({"event": {"subtype": "bot_message"}})
        assert SlackAdapter.is_bot_message({"event": {"bot_id": "B123"}})
        assert not SlackAdapter.is_bot_message({"event": {"text": "hi"}})


class TestTelegramAdapter:
    def _make_adapter(self, token="test-bot-token"):
        return get_adapter("telegram", bot_token=token)

    def test_normalize_payload(self):
        adapter = self._make_adapter()
        payload = {
            "message": {
                "text": "hello",
                "from": {"username": "alice", "id": 123},
                "chat": {"title": "My Group", "id": -456},
                "date": 1709734200,
                "message_id": 789,
            }
        }
        msg = adapter.normalize(payload)
        assert msg.content == "hello"
        assert msg.user == "alice"
        assert msg.channel == "My Group"
        assert msg.source_message_id == "789"
        assert "T" in msg.timestamp  # ISO 8601

    def test_user_fallback_to_id(self):
        adapter = self._make_adapter()
        payload = {
            "message": {
                "text": "hi",
                "from": {"id": 12345},
                "chat": {"id": -456},
                "date": 1709734200,
                "message_id": 1,
            }
        }
        msg = adapter.normalize(payload)
        assert msg.user == "12345"

    def test_verify_valid_token(self):
        token = "test-bot-token"
        adapter = self._make_adapter(token)
        expected_secret = hashlib.sha256(token.encode()).hexdigest()[:32]
        headers = {"x-telegram-bot-api-secret-token": expected_secret}
        assert adapter.verify(headers, b"") is True

    def test_verify_invalid_token(self):
        adapter = self._make_adapter()
        headers = {"x-telegram-bot-api-secret-token": "wrong"}
        assert adapter.verify(headers, b"") is False


class TestGitAdapter:
    def _make_adapter(self, secret=None):
        kwargs = {}
        if secret:
            kwargs["webhook_secret"] = secret
        return get_adapter("git", **kwargs)

    def test_normalize_github_payload(self):
        adapter = self._make_adapter()
        payload = {
            "commits": [
                {
                    "message": "feat: add auth",
                    "author": {"email": "alice@co.com"},
                    "id": "abc123",
                    "timestamp": "2026-03-06T14:30:00Z",
                }
            ],
            "repository": {"full_name": "org/repo"},
        }
        msg = adapter.normalize(payload)
        assert "feat: add auth" in msg.content
        assert msg.user == "alice@co.com"
        assert msg.channel == "org/repo"
        assert msg.source_message_id == "abc123"
        assert msg.memory_type == "code"
        assert "git-commit" in msg.tags
        assert msg.raw_format == "git_commit"

    def test_multi_commit_payload(self):
        adapter = self._make_adapter()
        payload = {
            "commits": [
                {"message": "commit 1", "author": {"email": "a@b.com"}, "id": "a1"},
                {"message": "commit 2", "author": {"email": "a@b.com"}, "id": "a2"},
                {"message": "commit 3", "author": {"email": "a@b.com"}, "id": "a3"},
            ],
            "repository": {"full_name": "org/repo"},
        }
        msg = adapter.normalize(payload)
        assert "commit 1" in msg.content
        assert "commit 2" in msg.content
        assert "commit 3" in msg.content

    def test_simple_format(self):
        adapter = self._make_adapter()
        payload = {
            "message": "fix bug",
            "author": "bob",
            "sha": "def456",
            "repo": "my-project",
        }
        msg = adapter.normalize(payload)
        assert "fix bug" in msg.content
        assert msg.user == "bob"
        assert msg.channel == "my-project"
        assert msg.source_message_id == "def456"

    def test_verify_github_signature(self):
        secret = "webhook-secret"
        adapter = self._make_adapter(secret)
        body = b'{"commits":[]}'
        expected = "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        headers = {"x-hub-signature-256": expected}
        assert adapter.verify(headers, body) is True

    def test_verify_invalid_signature(self):
        adapter = self._make_adapter("secret")
        headers = {"x-hub-signature-256": "sha256=invalid"}
        assert adapter.verify(headers, b"body") is False

    def test_verify_missing_prefix(self):
        adapter = self._make_adapter("secret")
        headers = {"x-hub-signature-256": "invalid"}
        assert adapter.verify(headers, b"body") is False

    def test_verify_no_secret_configured(self):
        adapter = self._make_adapter()
        assert adapter.verify({}, b"body") is True
