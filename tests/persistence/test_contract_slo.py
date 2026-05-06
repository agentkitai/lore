"""Contract tests for the SloOps slice of Store — SLO definition CRUD.

These tests run against every Store implementation (Phase 1K: Postgres only).
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from lore.persistence import (
    NewSloDefinition,
    SloDefinitionPatch,
    Store,
    StoredSloDefinition,
)

# ── helpers ────────────────────────────────────────────────────────────────────


async def _insert_slo(
    store,
    *,
    org_id: str = "test-org",
    name: str = "test-slo",
    metric: str = "p99_latency",
    operator: str = "gt",
    threshold: float = 200.0,
    window_minutes: int = 60,
    enabled: bool = True,
    alert_channels: list | None = None,
) -> str:
    """Insert a slo_definitions row via raw SQL and return its id."""
    from ulid import ULID

    slo_id = f"slo_{ULID()}"
    ac = alert_channels or []
    await store._conn.execute(
        """
        INSERT INTO slo_definitions
            (id, org_id, name, metric, operator, threshold,
             window_minutes, enabled, alert_channels)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
        """,
        slo_id,
        org_id,
        name,
        metric,
        operator,
        threshold,
        window_minutes,
        enabled,
        json.dumps(ac),
    )
    return slo_id


# ── list_slo_definitions ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_returns_org_only_when_filter_set(store: Store):
    await _insert_slo(store, org_id="org-list", name="slo-a")
    await _insert_slo(store, org_id="org-list", name="slo-b")
    await _insert_slo(store, org_id="other-org", name="slo-c")

    results = await store.list_slo_definitions("org-list")

    assert len(results) == 2
    for r in results:
        assert r.org_id == "org-list"
        assert isinstance(r, StoredSloDefinition)


@pytest.mark.asyncio
async def test_list_returns_all_when_org_filter_none(store: Store):
    await _insert_slo(store, org_id="org-all-a", name="slo-x")
    await _insert_slo(store, org_id="org-all-b", name="slo-y")

    results = await store.list_slo_definitions(None)

    # Should include rows from both orgs (no WHERE clause — preserves multi-tenancy quirk)
    org_ids = {r.org_id for r in results}
    assert "org-all-a" in org_ids
    assert "org-all-b" in org_ids


# ── get_slo_definition ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_none_when_missing(store: Store):
    result = await store.get_slo_definition("slo_does_not_exist", "org-x")
    assert result is None


@pytest.mark.asyncio
async def test_get_org_isolation(store: Store):
    slo_id = await _insert_slo(store, org_id="org-iso-a", name="slo-iso")

    # Correct org → found
    result = await store.get_slo_definition(slo_id, "org-iso-a")
    assert result is not None
    assert result.id == slo_id

    # Wrong org → None
    result_wrong = await store.get_slo_definition(slo_id, "org-iso-b")
    assert result_wrong is None


# ── create_slo_definition ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_round_trip(store: Store):
    channels = [{"type": "email", "address": "ops@example.com"}]
    slo = NewSloDefinition(
        org_id="org-create",
        name="my-slo",
        metric="error_rate",
        operator="gt",
        threshold=0.05,
        window_minutes=30,
        enabled=True,
        alert_channels=channels,
    )

    created = await store.create_slo_definition(slo)

    assert created.id.startswith("slo_")
    assert created.org_id == "org-create"
    assert created.name == "my-slo"
    assert created.metric == "error_rate"
    assert created.operator == "gt"
    assert created.threshold == 0.05
    assert created.window_minutes == 30
    assert created.enabled is True
    # alert_channels round-trips through JSONB
    assert len(created.alert_channels) == 1
    assert created.alert_channels[0]["type"] == "email"
    assert created.alert_channels[0]["address"] == "ops@example.com"
    assert isinstance(created.created_at, datetime)
    assert isinstance(created.updated_at, datetime)

    # Round-trip via get
    fetched = await store.get_slo_definition(created.id, "org-create")
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.alert_channels == created.alert_channels


# ── update_slo_definition ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_changes_field(store: Store):
    slo_id = await _insert_slo(
        store, org_id="org-upd", name="before-slo", threshold=100.0
    )

    patch = SloDefinitionPatch(name="after-slo", threshold=250.0)
    result = await store.update_slo_definition(slo_id, "org-upd", patch)

    assert result is not None
    assert result.name == "after-slo"
    assert result.threshold == 250.0
    assert result.id == slo_id

    # Verify persisted
    fetched = await store.get_slo_definition(slo_id, "org-upd")
    assert fetched is not None
    assert fetched.name == "after-slo"
    assert fetched.threshold == 250.0


@pytest.mark.asyncio
async def test_update_returns_none_when_missing(store: Store):
    patch = SloDefinitionPatch(name="ghost-slo")
    result = await store.update_slo_definition("slo_nonexistent", "org-x", patch)
    assert result is None


@pytest.mark.asyncio
async def test_update_empty_patch_raises_value_error(store: Store):
    slo_id = await _insert_slo(store, org_id="org-empty", name="empty-patch-slo")

    with pytest.raises(ValueError, match="empty patch"):
        await store.update_slo_definition(slo_id, "org-empty", SloDefinitionPatch())


# ── delete_slo_definition ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_returns_true_when_existed(store: Store):
    slo_id = await _insert_slo(store, org_id="org-del", name="to-delete-slo")

    result = await store.delete_slo_definition(slo_id, "org-del")

    assert result is True

    # Confirm gone
    fetched = await store.get_slo_definition(slo_id, "org-del")
    assert fetched is None


@pytest.mark.asyncio
async def test_delete_returns_false_when_missing(store: Store):
    result = await store.delete_slo_definition("slo_ghost", "org-ghost")
    assert result is False
