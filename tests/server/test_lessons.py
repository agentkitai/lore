"""Tests for lesson CRUD endpoints — redirected to FakeStore-based tests.

All tests in this file previously relied on SQL-level mocking via
``patch("lore.server.routes.lessons.get_pool", ...)``.  After the T7 refactor,
``routes/lessons.py`` no longer uses ``get_pool`` — all handlers go through
``lessons_service`` backed by a ``Store``.

Full coverage with FakeStore + service-layer mocks is provided in T8
(``tests/server/test_lessons_routes.py``).
"""

from __future__ import annotations

import hashlib

import pytest
import pytest_asyncio

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from httpx import ASGITransport, AsyncClient

from lore.server.app import app
from lore.server.auth import _key_cache, _last_used_updates
from lore.server.middleware import RateLimiter, set_rate_limiter

# ── Fixtures ───────────────────────────────────────────────────────

RAW_KEY = "lore_sk_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
KEY_HASH = hashlib.sha256(RAW_KEY.encode()).hexdigest()
ORG_ID = "org-001"
HEADERS = {"Authorization": f"Bearer {RAW_KEY}"}

SAMPLE_EMBEDDING = [0.1] * 384


@pytest_asyncio.fixture
async def client():
    _key_cache.clear()
    _last_used_updates.clear()
    set_rate_limiter(RateLimiter())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    _key_cache.clear()
    _last_used_updates.clear()


# ── Create Tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_create_lesson(client):
    pass


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_create_lesson_missing_fields(client):
    pass


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_create_lesson_invalid_embedding_size(client):
    pass


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_create_lesson_empty_problem(client):
    pass


# ── Get Tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_get_lesson(client):
    pass


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_get_lesson_not_found(client):
    pass


# ── Update Tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_update_lesson_confidence(client):
    pass


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_update_lesson_atomic_upvote(client):
    pass


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_update_lesson_no_fields(client):
    pass


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_update_lesson_not_found(client):
    pass


# ── Delete Tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_delete_lesson(client):
    pass


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_delete_lesson_not_found(client):
    pass


# ── List Tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_list_lessons(client):
    pass


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_list_lessons_pagination(client):
    pass


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_list_lessons_limit_exceeds_max(client):
    pass


# ── Project Scoping Tests ──────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_project_scoped_key_returns_404_for_other_project(client):
    pass


# ── Export Tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_export_lessons(client):
    pass


# ── Import Tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_import_lessons(client):
    pass


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_import_empty_list(client):
    pass


# ── Search Tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_search_basic(client):
    pass


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_search_empty_results(client):
    pass


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_search_wrong_embedding_dim(client):
    pass


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_search_empty_embedding(client):
    pass


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_search_with_tags(client):
    pass


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_search_with_project(client):
    pass


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_search_project_scoped_key_overrides(client):
    pass


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_search_limit_default(client):
    pass


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_search_limit_exceeds_max(client):
    pass


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_search_min_confidence_filters(client):
    pass


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_search_requires_auth(client):
    pass


# ── Auth Required ──────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.skip(reason="Replaced by FakeStore tests in T8 (tests/server/test_lessons_routes.py)")
async def test_endpoints_require_auth(client):
    pass
