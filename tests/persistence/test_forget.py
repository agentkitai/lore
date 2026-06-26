"""Forget-with-proof: GDPR erasure + signed deletion certificate (#81).

Pure-cert tests need no store; the integration tests use the parametrized
``store`` fixture (sqlite always, postgres when available).
"""

from __future__ import annotations

import pytest

from lore.persistence import NewMemory
from lore.services.forget import (
    build_deletion_certificate,
    forget_with_proof,
    verify_deletion_certificate,
)


def _vec(seed: int):
    return [((seed + i * 7) % 100) / 100.0 for i in range(384)]


# ── certificate (pure) ──────────────────────────────────────────────
def test_certificate_unsigned_by_default(monkeypatch):
    monkeypatch.delenv("LORE_DELETION_SIGNING_KEY", raising=False)
    c = build_deletion_certificate(org_id="o", deleted_ids=["b", "a"], requested_count=2, subject_user_id="u")
    assert c["kind"] == "lore.deletion-certificate/v1"
    assert c["deletedMemoryIds"] == ["a", "b"]  # sorted
    assert c["deletedCount"] == 2 and c["requestedCount"] == 2
    assert c["subject"]["userId"] == "u"
    assert c["contentHash"].startswith("sha256:")
    assert c["signature"] is None


def test_certificate_signed_with_key(monkeypatch):
    monkeypatch.setenv("LORE_DELETION_SIGNING_KEY", "deletion-key-at-least-16-chars")
    c = build_deletion_certificate(org_id="o", deleted_ids=["a"], requested_count=1)
    assert c["signature"]["type"] == "hmac"
    assert len(c["signature"]["value"]) == 64


def test_certificate_hash_changes_with_content(monkeypatch):
    monkeypatch.delenv("LORE_DELETION_SIGNING_KEY", raising=False)
    h1 = build_deletion_certificate(org_id="o", deleted_ids=["a"], requested_count=1)["contentHash"]
    h2 = build_deletion_certificate(org_id="o", deleted_ids=["a", "b"], requested_count=2)["contentHash"]
    assert h1 != h2


def test_verify_certificate_roundtrip_and_tamper(monkeypatch):
    monkeypatch.delenv("LORE_DELETION_SIGNING_KEY", raising=False)
    c = build_deletion_certificate(org_id="o", deleted_ids=["a", "b"], requested_count=2, subject_user_id="u")
    assert verify_deletion_certificate(c)["valid"] is True
    assert verify_deletion_certificate({**c, "deletedCount": 99})["valid"] is False


def test_verify_signed_certificate(monkeypatch):
    key = "deletion-key-at-least-16-chars"
    monkeypatch.setenv("LORE_DELETION_SIGNING_KEY", key)
    c = build_deletion_certificate(org_id="o", deleted_ids=["a"], requested_count=1)
    assert verify_deletion_certificate(c, signing_key=key)["valid"] is True
    assert verify_deletion_certificate(c, signing_key="wrong-key-also-16-chars!")["valid"] is False


# ── erasure (integration) ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_subject_erasure_deletes_only_owner_rows(store):
    a1 = await store.insert_memory(NewMemory(org_id="solo", content="a1", embedding=_vec(1), user_id="alice"))
    a2 = await store.insert_memory(NewMemory(org_id="solo", content="a2", embedding=_vec(2), user_id="alice"))
    b1 = await store.insert_memory(NewMemory(org_id="solo", content="b1", embedding=_vec(3), user_id="bob"))

    cert = await forget_with_proof(store, org_id="solo", user_id="alice")
    assert cert["deletedCount"] == 2
    assert set(cert["deletedMemoryIds"]) == {a1.id, a2.id}
    assert await store.get_memory("solo", a1.id) is None
    assert await store.get_memory("solo", b1.id) is not None  # bob's data untouched


@pytest.mark.asyncio
async def test_explicit_memory_ids_mode(store):
    m = await store.insert_memory(NewMemory(org_id="solo", content="x", embedding=_vec(1), user_id="u"))
    cert = await forget_with_proof(store, org_id="solo", memory_ids=[m.id])
    assert cert["deletedCount"] == 1
    assert cert["subject"]["userId"] is None
    assert await store.get_memory("solo", m.id) is None


@pytest.mark.asyncio
async def test_requires_user_id_or_memory_ids(store):
    with pytest.raises(ValueError):
        await forget_with_proof(store, org_id="solo")


@pytest.mark.asyncio
async def test_rejects_both_user_id_and_memory_ids(store):
    # Both → ambiguous subject↔ids association; must be rejected before any delete.
    with pytest.raises(ValueError):
        await forget_with_proof(store, org_id="solo", user_id="alice", memory_ids=["x"])
