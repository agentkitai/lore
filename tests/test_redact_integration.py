"""Integration tests: redaction wired into Lore.remember()."""

from __future__ import annotations

import pytest

from lore import Lore
from lore.exceptions import SecretBlockedError
from lore.store.memory import MemoryStore


def _make_lore(**kwargs) -> Lore:
    """Create a Lore instance with in-memory store and dummy embedder."""
    return Lore(

        store=MemoryStore(),
        embedding_fn=lambda text: [0.0] * 384,
        **kwargs,
    )


class TestRememberRedaction:
    def test_blocks_api_key(self) -> None:
        """API keys now trigger block (not mask) by default."""
        lore = _make_lore()
        with pytest.raises(SecretBlockedError, match="api_key"):
            lore.remember("Call sk-abc123def456ghi789jkl012 for help — fixed")

    def test_api_key_mask_override(self) -> None:
        """Users can override api_key to mask instead of block."""
        lore = _make_lore(security_action_overrides={"api_key": "mask"})
        mid = lore.remember("Call sk-abc123def456ghi789jkl012 for help — fixed")
        memory = lore.get(mid)
        assert memory is not None
        assert "[REDACTED:api_key]" in memory.content
        assert "sk-abc" not in memory.content

    def test_redacts_email(self) -> None:
        lore = _make_lore()
        mid = lore.remember("Email admin@secret.com for access — use the portal instead")
        memory = lore.get(mid)
        assert memory is not None
        assert "[REDACTED:email]" in memory.content

    def test_redacts_ip(self) -> None:
        lore = _make_lore()
        mid = lore.remember("Server 192.168.1.100 was down — restart it")
        memory = lore.get(mid)
        assert memory is not None
        assert "[REDACTED:ip_address]" in memory.content

    def test_redact_false_disables(self) -> None:
        lore = _make_lore(redact=False)
        mid = lore.remember("key sk-abc123def456ghi789jkl012 — fixed")
        memory = lore.get(mid)
        assert memory is not None
        assert "sk-abc123" in memory.content

    def test_custom_patterns(self) -> None:
        lore = _make_lore(redact_patterns=[(r"ACCT-\d+", "account_id")])
        mid = lore.remember("Check ACCT-99887766 — fixed")
        memory = lore.get(mid)
        assert memory is not None
        assert "[REDACTED:account_id]" in memory.content


class TestBlockAction:
    def test_api_key_blocks_storage(self) -> None:
        """Secrets should block storage entirely, not just mask."""
        lore = _make_lore()
        with pytest.raises(SecretBlockedError, match="api_key"):
            lore.remember("Use this key: sk-abc123def456ghi789jkl012")

    def test_jwt_blocks_storage(self) -> None:
        lore = _make_lore()
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        with pytest.raises(SecretBlockedError, match="jwt_token"):
            lore.remember(f"auth token: {jwt}")

    def test_private_key_blocks_storage(self) -> None:
        lore = _make_lore()
        with pytest.raises(SecretBlockedError, match="private_key"):
            lore.remember("-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIB...")

    def test_email_masks_not_blocks(self) -> None:
        """PII should mask, not block."""
        lore = _make_lore()
        mid = lore.remember("Contact admin@example.com for help")
        memory = lore.get(mid)
        assert memory is not None
        assert "[REDACTED:email]" in memory.content

    def test_override_api_key_to_mask(self) -> None:
        """Users can override block→mask for specific types."""
        lore = _make_lore(security_action_overrides={"api_key": "mask"})
        mid = lore.remember("key: sk-abc123def456ghi789jkl012")
        memory = lore.get(mid)
        assert memory is not None
        assert "[REDACTED:api_key]" in memory.content

    def test_context_also_scanned(self) -> None:
        """Context field should also trigger block."""
        lore = _make_lore()
        with pytest.raises(SecretBlockedError):
            lore.remember(
                "normal content",
                context="secret: sk-abc123def456ghi789jkl012",
            )

    def test_error_message_shows_type_not_value(self) -> None:
        """Error message should mention the type but not the secret."""
        lore = _make_lore()
        with pytest.raises(SecretBlockedError) as exc_info:
            lore.remember("key: sk-abc123def456ghi789jkl012")
        assert "api_key" in str(exc_info.value)
        assert "sk-abc" not in str(exc_info.value)
