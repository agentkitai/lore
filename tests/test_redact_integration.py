"""Integration tests: redaction wired into Lore.publish()."""

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


class TestPublishRedaction:
    def test_redacts_problem(self) -> None:
        lore = _make_lore()
        lid = lore.publish(
            problem="Call sk-abc123def456ghi789jkl012 for help",
            resolution="fixed",
        )
        lesson = lore.get(lid)
        assert lesson is not None
        assert "[REDACTED:api_key]" in lesson.problem
        assert "sk-abc" not in lesson.problem

    def test_redacts_resolution(self) -> None:
        lore = _make_lore()
        lid = lore.publish(
            problem="issue",
            resolution="Email admin@secret.com for access",
        )
        lesson = lore.get(lid)
        assert lesson is not None
        assert "[REDACTED:email]" in lesson.resolution

    def test_redacts_context(self) -> None:
        lore = _make_lore()
        lid = lore.publish(
            problem="issue",
            resolution="fixed",
            context="Server 192.168.1.100 was down",
        )
        lesson = lore.get(lid)
        assert lesson is not None
        assert "[REDACTED:ip_address]" in lesson.context  # type: ignore

    def test_redact_false_disables(self) -> None:
        lore = _make_lore(redact=False)
        lid = lore.publish(
            problem="key sk-abc123def456ghi789jkl012",
            resolution="fixed",
        )
        lesson = lore.get(lid)
        assert lesson is not None
        assert "sk-abc123" in lesson.problem

    def test_custom_patterns(self) -> None:
        lore = _make_lore(redact_patterns=[(r"ACCT-\d+", "account_id")])
        lid = lore.publish(
            problem="Check ACCT-99887766",
            resolution="fixed",
        )
        lesson = lore.get(lid)
        assert lesson is not None
        assert "[REDACTED:account_id]" in lesson.problem
