"""Integration tests: redaction wired into Lore.remember()."""

from __future__ import annotations

from lore import Lore
from lore.store.memory import MemoryStore


def _make_lore(**kwargs) -> Lore:
    """Create a Lore instance with in-memory store and dummy embedder."""
    return Lore(
        store=MemoryStore(),
        embedding_fn=lambda text: [0.0] * 384,
        **kwargs,
    )


class TestRememberRedaction:
    def test_redacts_content(self) -> None:
        lore = _make_lore()
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
