"""Write-side redaction (PR2): the shared helper, the env-config redactor, and
the create_memory wiring that applies it to every server/AsyncLore write.

The pure helper/factory tests need no DB; the create_memory tests use the
parametrized ``store`` fixture (Postgres + SQLite).
"""

from __future__ import annotations

from typing import Sequence

import pytest

from lore.exceptions import SecretBlockedError
from lore.redact.pipeline import RedactionPipeline
from lore.redact.write import get_write_redactor, redact_for_write
from lore.services.memories import create_memory

_API_KEY = "sk-abc123def456ghi789jkl012"


def _vec(seed: int = 1) -> Sequence[float]:
    return [((seed + i * 7) % 100) / 100.0 for i in range(384)]


@pytest.fixture(autouse=True)
def _isolate_redactor_cache(monkeypatch):
    """Each test starts from a clean, default redactor env + cache."""
    for var in ("LORE_REDACT_DISABLED", "LORE_REDACT_BLOCK", "LORE_REDACT_DENYLIST"):
        monkeypatch.delenv(var, raising=False)
    get_write_redactor.cache_clear()
    yield
    get_write_redactor.cache_clear()


# ── redact_for_write helper ────────────────────────────────────────


def test_helper_pass_through_when_disabled():
    content, ctx, meta = redact_for_write(None, f"key {_API_KEY}", "ctx")
    assert _API_KEY in content
    assert meta == {}


def test_helper_masks_email_and_tags():
    content, _ctx, meta = redact_for_write(RedactionPipeline(), "ping admin@secret.com now")
    assert "[REDACTED:email]" in content
    assert "admin@secret.com" not in content
    assert meta["redacted"] is True
    assert "email" in meta["redacted_types"]


def test_helper_blocks_on_block_action():
    # Default pipeline blocks secrets.
    with pytest.raises(SecretBlockedError, match="api_key"):
        redact_for_write(RedactionPipeline(), f"use {_API_KEY}")


def test_helper_scans_context_too():
    r = RedactionPipeline(security_action_overrides={"api_key": "mask"})
    _content, ctx, meta = redact_for_write(r, "clean", f"key {_API_KEY}")
    assert "[REDACTED:api_key]" in ctx
    assert "api_key" in meta["redacted_types"]


# ── get_write_redactor env config ──────────────────────────────────


def test_default_masks_secrets_not_blocks():
    content, _ctx, meta = redact_for_write(get_write_redactor(), f"use {_API_KEY}")
    assert "[REDACTED:api_key]" in content  # masked, write succeeds
    assert "api_key" in meta["redacted_types"]


def test_block_mode_blocks(monkeypatch):
    monkeypatch.setenv("LORE_REDACT_BLOCK", "1")
    get_write_redactor.cache_clear()
    with pytest.raises(SecretBlockedError):
        redact_for_write(get_write_redactor(), f"use {_API_KEY}")


def test_disabled_returns_none(monkeypatch):
    monkeypatch.setenv("LORE_REDACT_DISABLED", "1")
    get_write_redactor.cache_clear()
    assert get_write_redactor() is None


def test_denylist_file(monkeypatch, tmp_path):
    f = tmp_path / "deny.txt"
    f.write_text("# names + domains\nAcme Corp\nre:PROJ-\\d+\n", encoding="utf-8")
    monkeypatch.setenv("LORE_REDACT_DENYLIST", str(f))
    get_write_redactor.cache_clear()
    content, _ctx, meta = redact_for_write(get_write_redactor(), "ticket PROJ-42 for Acme Corp")
    assert "Acme Corp" not in content
    assert "PROJ-42" not in content
    assert content.count("[REDACTED:denylisted]") == 2


# ── create_memory wiring (every server/AsyncLore write) ────────────


@pytest.mark.asyncio
async def test_create_memory_masks_and_tags(store):
    m = await create_memory(
        store, org_id="solo", content="reach me at admin@secret.com", embedding=_vec(1)
    )
    assert "[REDACTED:email]" in m.content
    assert "admin@secret.com" not in m.content
    assert m.meta.get("redacted") is True
    assert "email" in m.meta.get("redacted_types", [])


@pytest.mark.asyncio
async def test_create_memory_api_key_masked_by_default(store):
    m = await create_memory(store, org_id="solo", content=f"key {_API_KEY}", embedding=_vec(1))
    assert "[REDACTED:api_key]" in m.content
    assert _API_KEY not in m.content


@pytest.mark.asyncio
async def test_create_memory_clean_content_untouched(store):
    m = await create_memory(
        store, org_id="solo", content="just a normal note about caching", embedding=_vec(1)
    )
    assert m.content == "just a normal note about caching"
    assert "redacted" not in m.meta


@pytest.mark.asyncio
async def test_create_memory_block_mode_raises(store, monkeypatch):
    monkeypatch.setenv("LORE_REDACT_BLOCK", "1")
    get_write_redactor.cache_clear()
    with pytest.raises(SecretBlockedError):
        await create_memory(store, org_id="solo", content=f"use {_API_KEY}", embedding=_vec(1))


# ── Other write paths that build NewMemory directly ────────────────


@pytest.mark.asyncio
async def test_lessons_create_redacts(store):
    from lore.services import lessons as lessons_service

    lid = await lessons_service.create(
        store, org_id="solo", problem="ping admin@secret.com", resolution="ip 10.0.0.5",
        context=None, tags=None, source=None, project=None, embedding=None,
        expires_at=None, meta=None,
    )
    m = await store.get_memory("solo", lid)
    assert "[REDACTED:email]" in m.content
    assert "[REDACTED:ip_address]" in (m.context or "")
    assert m.meta.get("redacted") is True


@pytest.mark.asyncio
async def test_snapshot_redacts_content_and_title(store):
    from lore.services.snapshots import create_snapshot

    m = await create_snapshot(store, org_id="solo", content="note: reach admin@secret.com here")
    assert "[REDACTED:email]" in m.content
    assert "admin@secret.com" not in m.content
    assert "admin@secret.com" not in m.meta.get("title", "")
    assert m.meta.get("redacted") is True
