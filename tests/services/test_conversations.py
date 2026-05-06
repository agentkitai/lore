"""Service-level tests for lore.services.conversations using a real Postgres store."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from lore.persistence.exceptions import StoreNotFoundError
from lore.services import conversations as svc

# ── helpers ───────────────────────────────────────────────────────────────────

_ORG = "solo"
_MSGS = [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi"}]


class _FakeMem:
    def __init__(
        self,
        id,
        content="x",
        metadata=None,
        type=None,
        source=None,
        tags=None,
        confidence=0.9,
    ):
        self.id = id
        self.content = content
        self.metadata = metadata or {}
        self.type = type
        self.source = source
        self.tags = tags or []
        self.confidence = confidence


class _FakeMemStore:
    def __init__(self, by_id):
        self._by_id = by_id

    def get(self, mid):
        return self._by_id.get(mid)


class _FakeLore:
    def __init__(self, by_id=None):
        self._store = _FakeMemStore(by_id or {})
        self.closed = False

    def close(self):
        self.closed = True


class _FakeResult:
    memory_ids = []
    memories_extracted = 0
    duplicates_skipped = 0


class _FakeExtractor:
    def __init__(self, lore):
        self.lore = lore

    def extract(self, *_, **__):
        return _FakeResult()


# ── validation tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_job_validates_empty_messages(store):
    """Empty messages list raises ValueError."""
    with pytest.raises(ValueError, match="messages must be non-empty"):
        await svc.create_job(store, org_id=_ORG, messages=[])


@pytest.mark.asyncio
async def test_create_job_validates_message_shape(store):
    """Message missing 'role' raises ValueError."""
    with pytest.raises(ValueError):
        await svc.create_job(store, org_id=_ORG, messages=[{"content": "no role"}])


# ── create_job ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_job_persists_and_returns_stored(store):
    """Happy path: create_job returns StoredConversationJob with status=accepted."""
    from lore.persistence import StoredConversationJob

    job = await svc.create_job(store, org_id=_ORG, messages=_MSGS)

    assert isinstance(job, StoredConversationJob)
    assert job.status == "accepted"
    assert job.message_count == len(_MSGS)
    assert job.org_id == _ORG


# ── get_job_status ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_job_status_returns_stored_job(store):
    """create then get returns matching job."""
    created = await svc.create_job(store, org_id=_ORG, messages=_MSGS)
    fetched = await svc.get_job_status(store, created.id, _ORG)

    assert fetched.id == created.id
    assert fetched.status == created.status


@pytest.mark.asyncio
async def test_get_job_status_raises_not_found(store):
    """Random id raises StoreNotFoundError."""
    with pytest.raises(StoreNotFoundError):
        await svc.get_job_status(store, f"job_{uuid.uuid4().hex}", _ORG)


@pytest.mark.asyncio
async def test_get_job_status_org_mismatch_raises_not_found(store):
    """Job created under org_a is not visible to org_b."""
    created = await svc.create_job(store, org_id="org_a", messages=_MSGS)

    with pytest.raises(StoreNotFoundError):
        await svc.get_job_status(store, created.id, "org_b")


# ── process_job_async ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_job_async_marks_complete_on_success(store, monkeypatch):
    """Successful extraction results in status=completed."""
    monkeypatch.setattr("lore.conversation.ConversationExtractor", _FakeExtractor)
    monkeypatch.setattr(
        "lore.services.conversations._get_server_lore",
        lambda *_, **__: _FakeLore(),
    )

    job = await svc.create_job(store, org_id=_ORG, messages=_MSGS)
    await svc.process_job_async(store, job.id, _ORG)

    finished = await store.get_conversation_job(job.id, _ORG)
    assert finished is not None
    assert finished.status == "completed"


@pytest.mark.asyncio
async def test_process_job_async_marks_failed_on_exception(store, monkeypatch):
    """Extractor raising RuntimeError results in status=failed with error set."""

    class _BoomExtractor:
        def __init__(self, lore):
            pass

        def extract(self, *_, **__):
            raise RuntimeError("boom")

    monkeypatch.setattr("lore.conversation.ConversationExtractor", _BoomExtractor)
    monkeypatch.setattr(
        "lore.services.conversations._get_server_lore",
        lambda *_, **__: _FakeLore(),
    )

    job = await svc.create_job(store, org_id=_ORG, messages=_MSGS)
    await svc.process_job_async(store, job.id, _ORG)

    finished = await store.get_conversation_job(job.id, _ORG)
    assert finished is not None
    assert finished.status == "failed"
    assert "boom" in (finished.error or "")


@pytest.mark.asyncio
async def test_process_job_async_imports_extracted_memories(store, monkeypatch):
    """Extractor returning memory_ids triggers import_extracted_memory calls."""
    mem_a = _FakeMem("mem_x", content="fact A", type="fact", source="conversation")
    mem_b = _FakeMem("mem_y", content="fact B", type="lesson", source="conversation")
    fake_lore = _FakeLore(by_id={"mem_x": mem_a, "mem_y": mem_b})

    class _ResultWithMems:
        memory_ids = ["mem_x", "mem_y"]
        memories_extracted = 2
        duplicates_skipped = 0

    class _ExtractorWithMems:
        def __init__(self, lore):
            pass

        def extract(self, *_, **__):
            return _ResultWithMems()

    monkeypatch.setattr("lore.conversation.ConversationExtractor", _ExtractorWithMems)
    monkeypatch.setattr(
        "lore.services.conversations._get_server_lore",
        lambda *_, **__: fake_lore,
    )

    # Monkeypatch store.import_extracted_memory with AsyncMock to capture calls
    mock_import = AsyncMock(return_value=None)
    monkeypatch.setattr(store, "import_extracted_memory", mock_import)

    job = await svc.create_job(store, org_id=_ORG, messages=_MSGS)
    await svc.process_job_async(store, job.id, _ORG)

    assert mock_import.call_count == 2
    call_kwargs_list = [call.kwargs for call in mock_import.call_args_list]
    memory_ids_called = {kw["memory_id"] for kw in call_kwargs_list}
    assert memory_ids_called == {"mem_x", "mem_y"}
    for kw in call_kwargs_list:
        assert kw["org_id"] == _ORG


@pytest.mark.asyncio
async def test_process_job_async_skips_missing_id(store, monkeypatch):
    """Extractor returns memory_ids but store.get returns None — import not called."""
    fake_lore = _FakeLore(by_id={})  # get() always returns None

    class _ResultWithMissing:
        memory_ids = ["mem_missing"]
        memories_extracted = 0
        duplicates_skipped = 0

    class _ExtractorMissing:
        def __init__(self, lore):
            pass

        def extract(self, *_, **__):
            return _ResultWithMissing()

    monkeypatch.setattr("lore.conversation.ConversationExtractor", _ExtractorMissing)
    monkeypatch.setattr(
        "lore.services.conversations._get_server_lore",
        lambda *_, **__: fake_lore,
    )

    mock_import = AsyncMock(return_value=None)
    monkeypatch.setattr(store, "import_extracted_memory", mock_import)

    job = await svc.create_job(store, org_id=_ORG, messages=_MSGS)
    await svc.process_job_async(store, job.id, _ORG)

    mock_import.assert_not_called()


@pytest.mark.asyncio
async def test_process_job_async_handles_missing_job(store, monkeypatch):
    """Calling process_job_async with a non-existent job_id logs and returns cleanly."""
    monkeypatch.setattr("lore.conversation.ConversationExtractor", _FakeExtractor)
    monkeypatch.setattr(
        "lore.services.conversations._get_server_lore",
        lambda *_, **__: _FakeLore(),
    )

    # Should not raise
    await svc.process_job_async(store, f"job_{uuid.uuid4().hex}", _ORG)
