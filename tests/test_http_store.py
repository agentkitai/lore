"""Unit tests for HttpStore (mocked HTTP)."""

from __future__ import annotations

import struct
from unittest.mock import MagicMock, patch

import httpx
import pytest

from lore.exceptions import LoreAuthError, LoreConnectionError
from lore.store.http import HttpStore
from lore.types import Memory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(
    api_url: str = "http://localhost:8765",
    api_key: str = "lore_sk_testkey123",
    **kwargs,
) -> HttpStore:
    """Create an HttpStore with the health check mocked out."""
    with patch.object(HttpStore, "_check_health"):
        return HttpStore(api_url=api_url, api_key=api_key, **kwargs)


def _mock_response(status_code: int = 200, json_data=None, text: str = "") -> httpx.Response:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


# ---------------------------------------------------------------------------
# Constructor tests
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_creates_client_with_auth_header(self):
        store = _make_store()
        assert store._client.headers["authorization"] == "Bearer lore_sk_testkey123"
        store.close()

    def test_missing_api_url_raises(self):
        with pytest.raises(ValueError, match="api_url is required"):
            with patch.object(HttpStore, "_check_health"):
                HttpStore(api_key="lore_sk_x")

    def test_missing_api_key_raises(self):
        with pytest.raises(ValueError, match="api_key is required"):
            with patch.object(HttpStore, "_check_health"):
                HttpStore(api_url="http://localhost:8765")

    def test_env_var_fallback(self, monkeypatch):
        monkeypatch.setenv("LORE_API_URL", "http://env-server:9999")
        monkeypatch.setenv("LORE_API_KEY", "lore_sk_envkey")
        monkeypatch.setenv("LORE_HTTP_TIMEOUT", "15")
        with patch.object(HttpStore, "_check_health"):
            store = HttpStore()
        assert store._api_url == "http://env-server:9999"
        assert store._api_key == "lore_sk_envkey"
        assert store._timeout == 15.0
        store.close()

    def test_explicit_params_override_env(self, monkeypatch):
        monkeypatch.setenv("LORE_API_URL", "http://env-server:9999")
        monkeypatch.setenv("LORE_API_KEY", "lore_sk_envkey")
        store = _make_store(api_url="http://explicit:1234", api_key="lore_sk_explicit")
        assert store._api_url == "http://explicit:1234"
        assert store._api_key == "lore_sk_explicit"
        store.close()

    def test_strips_trailing_slash(self):
        store = _make_store(api_url="http://localhost:8765/")
        assert store._api_url == "http://localhost:8765"
        store.close()


# ---------------------------------------------------------------------------
# Health check tests
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_health_check_success(self):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.get.return_value = _mock_response(200)
            mock_client.headers = {"Authorization": "Bearer lore_sk_testkey123"}

            with patch.object(HttpStore, "__init__", lambda self, **kw: None):
                store = HttpStore.__new__(HttpStore)
                store._client = mock_client
                store._api_url = "http://localhost:8765"
                store._check_health()
                mock_client.get.assert_called_once_with("/health", timeout=5.0)

    def test_health_check_connect_error(self):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.get.side_effect = httpx.ConnectError("refused")

            with patch.object(HttpStore, "__init__", lambda self, **kw: None):
                store = HttpStore.__new__(HttpStore)
                store._client = mock_client
                store._api_url = "http://localhost:8765"
                with pytest.raises(LoreConnectionError, match="Cannot connect"):
                    store._check_health()

    def test_health_check_timeout(self):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.get.side_effect = httpx.ReadTimeout("timeout")

            with patch.object(HttpStore, "__init__", lambda self, **kw: None):
                store = HttpStore.__new__(HttpStore)
                store._client = mock_client
                store._api_url = "http://localhost:8765"
                with pytest.raises(LoreConnectionError, match="did not respond"):
                    store._check_health()

    def test_health_check_http_error(self):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            resp = _mock_response(503)
            mock_client.get.return_value = resp

            with patch.object(HttpStore, "__init__", lambda self, **kw: None):
                store = HttpStore.__new__(HttpStore)
                store._client = mock_client
                store._api_url = "http://localhost:8765"
                with pytest.raises(LoreConnectionError, match="returned 503"):
                    store._check_health()


# ---------------------------------------------------------------------------
# _request() error handling and retry tests
# ---------------------------------------------------------------------------

class TestRequest:
    def test_auth_error_on_401(self):
        store = _make_store()
        store._client.request = MagicMock(return_value=_mock_response(401))
        with pytest.raises(LoreAuthError, match="Invalid API key"):
            store._request("GET", "/v1/lessons/x")
        store.close()

    def test_auth_error_on_403(self):
        store = _make_store()
        store._client.request = MagicMock(return_value=_mock_response(403))
        with pytest.raises(LoreAuthError, match="Insufficient permissions"):
            store._request("GET", "/v1/lessons/x")
        store.close()

    def test_returns_404_response(self):
        store = _make_store()
        resp = _mock_response(404)
        store._client.request = MagicMock(return_value=resp)
        result = store._request("GET", "/v1/lessons/x")
        assert result.status_code == 404
        store.close()

    def test_422_raises_value_error(self):
        store = _make_store()
        resp = _mock_response(422, json_data={"detail": "Bad embedding dim"})
        store._client.request = MagicMock(return_value=resp)
        with pytest.raises(ValueError, match="Bad embedding dim"):
            store._request("POST", "/v1/lessons")
        store.close()

    @patch("lore.store.http.time.sleep")
    def test_retry_on_500(self, mock_sleep):
        store = _make_store()
        resp_500 = _mock_response(500, text="Internal Server Error")
        resp_200 = _mock_response(200)
        store._client.request = MagicMock(side_effect=[resp_500, resp_200])
        result = store._request("GET", "/v1/lessons")
        assert result.status_code == 200
        assert store._client.request.call_count == 2
        mock_sleep.assert_called_once_with(0.5)
        store.close()

    @patch("lore.store.http.time.sleep")
    def test_retry_exhausted_on_500(self, mock_sleep):
        store = _make_store()
        resp_500 = _mock_response(500, text="fail")
        store._client.request = MagicMock(return_value=resp_500)
        with pytest.raises(LoreConnectionError, match="Server error 500"):
            store._request("GET", "/v1/lessons")
        assert store._client.request.call_count == 3  # 1 + 2 retries
        store.close()

    @patch("lore.store.http.time.sleep")
    def test_retry_on_connect_error(self, mock_sleep):
        store = _make_store()
        resp_200 = _mock_response(200)
        store._client.request = MagicMock(
            side_effect=[httpx.ConnectError("refused"), resp_200]
        )
        result = store._request("GET", "/v1/lessons")
        assert result.status_code == 200
        store.close()

    @patch("lore.store.http.time.sleep")
    def test_retry_on_timeout(self, mock_sleep):
        store = _make_store()
        resp_200 = _mock_response(200)
        store._client.request = MagicMock(
            side_effect=[httpx.ReadTimeout("timeout"), resp_200]
        )
        result = store._request("GET", "/v1/lessons")
        assert result.status_code == 200
        store.close()

    def test_no_retry_on_4xx(self):
        store = _make_store()
        store._client.request = MagicMock(return_value=_mock_response(401))
        with pytest.raises(LoreAuthError):
            store._request("GET", "/v1/lessons")
        assert store._client.request.call_count == 1
        store.close()


# ---------------------------------------------------------------------------
# repr and close tests
# ---------------------------------------------------------------------------

class TestReprAndClose:
    def test_repr_masks_key(self):
        store = _make_store(api_key="lore_sk_570ce9f86812d86689c3ad45739b9ba0")
        r = repr(store)
        assert "lore_sk_" in r
        assert "570ce9f86812d86689c3ad45739b9ba0" not in r
        assert "***" in r
        store.close()

    def test_repr_short_key(self):
        store = _make_store(api_key="short")
        r = repr(store)
        assert "***" in r
        store.close()

    def test_close_closes_client(self):
        store = _make_store()
        store._client.close = MagicMock()
        store.close()
        store._client.close.assert_called_once()

    def test_close_idempotent(self):
        store = _make_store()
        store._client.close = MagicMock()
        store.close()
        store.close()
        store._client.close.assert_called_once()

    def test_api_key_not_in_error_messages(self):
        store = _make_store(api_key="lore_sk_secretvalue")
        store._client.request = MagicMock(return_value=_mock_response(401))
        with pytest.raises(LoreAuthError) as exc_info:
            store._request("GET", "/test")
        assert "secretvalue" not in str(exc_info.value)
        store.close()


# ---------------------------------------------------------------------------
# Field mapping tests
# ---------------------------------------------------------------------------

def _make_memory(**overrides) -> Memory:
    defaults = dict(
        id="test-id",
        content="Always use retries",
        type="lesson",
        context="HTTP calls",
        tags=["http", "retry"],
        metadata={"key": "val"},
        source="test",
        project="myproject",
        embedding=struct.pack("384f", *([0.1] * 384)),
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        ttl=None,
        expires_at=None,
        confidence=0.9,
        upvotes=2,
        downvotes=1,
    )
    defaults.update(overrides)
    return Memory(**defaults)


class TestMemoryToLesson:
    def test_basic_mapping(self):
        mem = _make_memory()
        lesson = HttpStore._memory_to_lesson(mem)
        assert lesson["problem"] == "Always use retries"
        assert lesson["resolution"] == "Always use retries"
        assert lesson["context"] == "HTTP calls"
        assert lesson["tags"] == ["http", "retry"]
        assert lesson["confidence"] == 0.9
        assert lesson["source"] == "test"
        assert lesson["project"] == "myproject"

    def test_type_stored_in_meta(self):
        mem = _make_memory(type="code")
        lesson = HttpStore._memory_to_lesson(mem)
        assert lesson["meta"]["type"] == "code"

    def test_embedding_deserialized(self):
        mem = _make_memory()
        lesson = HttpStore._memory_to_lesson(mem)
        assert isinstance(lesson["embedding"], list)
        assert len(lesson["embedding"]) == 384
        assert abs(lesson["embedding"][0] - 0.1) < 1e-5

    def test_no_embedding(self):
        mem = _make_memory(embedding=None)
        lesson = HttpStore._memory_to_lesson(mem)
        assert "embedding" not in lesson

    def test_ttl_to_expires_at(self):
        mem = _make_memory(ttl=3600, expires_at=None)
        lesson = HttpStore._memory_to_lesson(mem)
        assert lesson["expires_at"] is not None

    def test_explicit_expires_at_not_overwritten(self):
        mem = _make_memory(ttl=3600, expires_at="2099-01-01T00:00:00+00:00")
        lesson = HttpStore._memory_to_lesson(mem)
        assert lesson["expires_at"] == "2099-01-01T00:00:00+00:00"

    def test_metadata_preserved(self):
        mem = _make_memory(metadata={"custom": "data"})
        lesson = HttpStore._memory_to_lesson(mem)
        assert lesson["meta"]["custom"] == "data"
        assert lesson["meta"]["type"] == "lesson"


class TestLessonToMemory:
    def test_basic_mapping(self):
        data = {
            "id": "srv-123",
            "problem": "Use retries",
            "resolution": "Use retries",
            "context": "HTTP",
            "tags": ["retry"],
            "confidence": 0.8,
            "source": "test",
            "project": "proj",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "expires_at": None,
            "upvotes": 3,
            "downvotes": 1,
            "meta": {"type": "lesson", "extra": "info"},
        }
        mem = HttpStore._lesson_to_memory(data)
        assert mem.id == "srv-123"
        assert mem.content == "Use retries"
        assert mem.type == "lesson"
        assert mem.tags == ["retry"]
        assert mem.confidence == 0.8
        assert mem.upvotes == 3
        assert mem.embedding is None

    def test_type_extracted_from_meta(self):
        data = {
            "id": "x", "problem": "p", "resolution": "p",
            "meta": {"type": "code"},
        }
        mem = HttpStore._lesson_to_memory(data)
        assert mem.type == "code"
        # type should not remain in metadata
        assert mem.metadata is None or "type" not in mem.metadata

    def test_default_type_is_general(self):
        data = {"id": "x", "problem": "p", "resolution": "p", "meta": {}}
        mem = HttpStore._lesson_to_memory(data)
        assert mem.type == "general"

    def test_resolution_different_from_problem(self):
        data = {
            "id": "x", "problem": "Error occurs",
            "resolution": "Fix by retrying", "meta": {},
        }
        mem = HttpStore._lesson_to_memory(data)
        assert mem.content == "Error occurs"
        assert mem.metadata["_resolution"] == "Fix by retrying"

    def test_resolution_same_as_problem_no_meta(self):
        data = {
            "id": "x", "problem": "Same", "resolution": "Same", "meta": {},
        }
        mem = HttpStore._lesson_to_memory(data)
        assert mem.metadata is None or "_resolution" not in (mem.metadata or {})

    def test_datetime_objects_converted(self):
        from datetime import datetime, timezone
        data = {
            "id": "x", "problem": "p", "resolution": "p", "meta": {},
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        }
        mem = HttpStore._lesson_to_memory(data)
        assert "2026-01-01" in mem.created_at
        assert "2026-01-02" in mem.updated_at


# ---------------------------------------------------------------------------
# CRUD method tests
# ---------------------------------------------------------------------------

class TestSave:
    def test_save_posts_lesson(self):
        store = _make_store()
        mem = _make_memory()
        store._client.request = MagicMock(
            return_value=_mock_response(201, json_data={"id": "srv-001"})
        )
        store.save(mem)
        call_args = store._client.request.call_args
        assert call_args[0] == ("POST", "/v1/lessons")
        body = call_args[1]["json"]
        assert body["problem"] == "Always use retries"
        assert body["resolution"] == "Always use retries"
        assert body["meta"]["type"] == "lesson"
        store.close()

    def test_save_overwrites_id(self):
        store = _make_store()
        mem = _make_memory(id="old-id")
        store._client.request = MagicMock(
            return_value=_mock_response(201, json_data={"id": "srv-new"})
        )
        store.save(mem)
        assert mem.id == "srv-new"
        store.close()

    def test_save_with_embedding(self):
        store = _make_store()
        mem = _make_memory()
        store._client.request = MagicMock(
            return_value=_mock_response(201, json_data={"id": "x"})
        )
        store.save(mem)
        body = store._client.request.call_args[1]["json"]
        assert len(body["embedding"]) == 384
        store.close()


class TestGet:
    def test_get_returns_memory(self):
        store = _make_store()
        lesson_data = {
            "id": "srv-1", "problem": "content", "resolution": "content",
            "context": None, "tags": ["a"], "confidence": 0.9,
            "source": "s", "project": "p",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "expires_at": None, "upvotes": 0, "downvotes": 0,
            "meta": {"type": "general"},
        }
        store._client.request = MagicMock(
            return_value=_mock_response(200, json_data=lesson_data)
        )
        mem = store.get("srv-1")
        assert mem is not None
        assert mem.id == "srv-1"
        assert mem.content == "content"
        store.close()

    def test_get_not_found(self):
        store = _make_store()
        store._client.request = MagicMock(return_value=_mock_response(404))
        assert store.get("nonexistent") is None
        store.close()


class TestList:
    def test_list_with_filters(self):
        store = _make_store()
        lesson = {
            "id": "1", "problem": "p", "resolution": "p",
            "meta": {"type": "lesson"},
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        store._client.request = MagicMock(
            return_value=_mock_response(200, json_data={
                "lessons": [lesson], "total": 1, "limit": 50, "offset": 0,
            })
        )
        result = store.list(project="proj", limit=10)
        params = store._client.request.call_args[1]["params"]
        assert params["project"] == "proj"
        assert params["limit"] == 10
        assert len(result) == 1
        store.close()

    def test_list_type_postfilter(self):
        store = _make_store()
        lessons = [
            {"id": "1", "problem": "p", "resolution": "p", "meta": {"type": "lesson"}},
            {"id": "2", "problem": "p", "resolution": "p", "meta": {"type": "code"}},
        ]
        store._client.request = MagicMock(
            return_value=_mock_response(200, json_data={
                "lessons": lessons, "total": 2, "limit": 50, "offset": 0,
            })
        )
        result = store.list(type="code")
        assert len(result) == 1
        assert result[0].type == "code"
        store.close()


class TestUpdate:
    def test_update_sends_patch(self):
        store = _make_store()
        mem = _make_memory(id="srv-1")
        store._client.request = MagicMock(
            return_value=_mock_response(200, json_data={
                "id": "srv-1", "problem": "p", "resolution": "p", "meta": {},
            })
        )
        result = store.update(mem)
        assert result is True
        call_args = store._client.request.call_args
        assert call_args[0] == ("PATCH", "/v1/lessons/srv-1")
        store.close()

    def test_update_not_found(self):
        store = _make_store()
        mem = _make_memory(id="gone")
        store._client.request = MagicMock(return_value=_mock_response(404))
        assert store.update(mem) is False
        store.close()


class TestDelete:
    def test_delete_success(self):
        store = _make_store()
        store._client.request = MagicMock(return_value=_mock_response(204))
        assert store.delete("srv-1") is True
        store.close()

    def test_delete_not_found(self):
        store = _make_store()
        store._client.request = MagicMock(return_value=_mock_response(404))
        assert store.delete("gone") is False
        store.close()


class TestCount:
    def test_count_uses_total(self):
        store = _make_store()
        store._client.request = MagicMock(
            return_value=_mock_response(200, json_data={
                "lessons": [], "total": 42, "limit": 1, "offset": 0,
            })
        )
        assert store.count(project="p") == 42
        params = store._client.request.call_args[1]["params"]
        assert params["limit"] == 1
        store.close()


class TestCleanupExpired:
    def test_returns_zero(self):
        store = _make_store()
        assert store.cleanup_expired() == 0
        store.close()


# ---------------------------------------------------------------------------
# Search tests
# ---------------------------------------------------------------------------

class TestSearch:
    def test_search_posts_embedding(self):
        store = _make_store()
        search_response = {
            "lessons": [
                {
                    "id": "s1", "problem": "use retries", "resolution": "use retries",
                    "meta": {"type": "lesson"}, "score": 0.85,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                },
            ],
        }
        store._client.request = MagicMock(
            return_value=_mock_response(200, json_data=search_response)
        )
        query_vec = [0.1] * 384
        results = store.search(embedding=query_vec, limit=5)
        # First call is the search POST, subsequent calls are access tracking
        search_call = store._client.request.call_args_list[0]
        assert search_call[0] == ("POST", "/v1/lessons/search")
        body = search_call[1]["json"]
        assert len(body["embedding"]) == 384
        assert len(results) == 1
        assert results[0].score == 0.85
        assert results[0].memory.content == "use retries"
        # Verify access tracking was called
        access_call = store._client.request.call_args_list[1]
        assert access_call[0] == ("POST", "/v1/lessons/s1/access")
        store.close()

    def test_search_with_filters(self):
        store = _make_store()
        store._client.request = MagicMock(
            return_value=_mock_response(200, json_data={"lessons": []})
        )
        store.search(
            embedding=[0.0] * 384,
            tags=["http"],
            project="proj",
            limit=10,
            min_confidence=0.5,
        )
        body = store._client.request.call_args[1]["json"]
        assert body["tags"] == ["http"]
        assert body["project"] == "proj"
        assert body["limit"] == 10
        assert body["min_confidence"] == 0.5
        store.close()

    def test_search_empty_results(self):
        store = _make_store()
        store._client.request = MagicMock(
            return_value=_mock_response(200, json_data={"lessons": []})
        )
        results = store.search(embedding=[0.0] * 384)
        assert results == []
        store.close()


# ---------------------------------------------------------------------------
# Lore.recall() dispatch tests
# ---------------------------------------------------------------------------

class TestRecallDispatch:
    def test_recall_delegates_to_search_when_available(self):
        store = _make_store()
        store.search = MagicMock(return_value=[])
        store.list = MagicMock(return_value=[])

        from lore.lore import Lore
        with patch.object(Lore, "__init__", lambda self, **kw: None):
            lore = Lore.__new__(Lore)
            lore._store = store
            lore._embedder = MagicMock()
            lore._embedder.embed.return_value = [0.1] * 384
            lore.project = "test"
            lore._last_cleanup = 0.0
            lore._last_cleanup_count = 0
            lore._half_life_days = 30
            lore._half_lives = {}
            lore._importance_threshold = 0.05
            lore._decay_config = None
            lore._tier_weights = {"working": 1.0, "short": 1.1, "long": 1.2}

            results = lore.recall("test query")
            store.search.assert_called_once()
            assert results == []

        store.close()

    def test_recall_uses_local_for_stores_without_search(self):
        from lore.lore import Lore
        from lore.store.memory import MemoryStore

        mem_store = MemoryStore()

        with patch.object(Lore, "__init__", lambda self, **kw: None):
            lore = Lore.__new__(Lore)
            lore._store = mem_store
            lore._embedder = MagicMock()
            lore._embedder.embed.return_value = [0.1] * 384
            lore.project = None
            lore._last_cleanup = 0.0
            lore._last_cleanup_count = 0
            lore._half_life_days = 30
            lore._half_lives = {}
            lore._importance_threshold = 0.05
            lore._decay_config = None
            lore._tier_weights = {"working": 1.0, "short": 1.1, "long": 1.2}

            # MemoryStore has no search() method
            assert not hasattr(mem_store, 'search')
            results = lore.recall("test query")
            assert isinstance(results, list)

    def test_recall_uses_prose_vec_for_search(self):
        store = _make_store()
        store.search = MagicMock(return_value=[])
        store.list = MagicMock(return_value=[])

        from lore.embed.router import EmbeddingRouter
        from lore.lore import Lore

        with patch.object(Lore, "__init__", lambda self, **kw: None):
            lore = Lore.__new__(Lore)
            lore._store = store
            mock_router = MagicMock(spec=EmbeddingRouter)
            mock_router.embed_query_dual.return_value = {
                "prose": [0.2] * 384,
                "code": [0.3] * 384,
            }
            lore._embedder = mock_router
            lore.project = "test"
            lore._last_cleanup = 0.0
            lore._last_cleanup_count = 0
            lore._half_life_days = 30
            lore._half_lives = {}
            lore._importance_threshold = 0.05
            lore._decay_config = None
            lore._tier_weights = {"working": 1.0, "short": 1.1, "long": 1.2}
            lore._dual_embedding = True

            lore.recall("test query")
            # Should use prose vec
            call_kwargs = store.search.call_args
            assert call_kwargs[1]["embedding"] == [0.2] * 384

        store.close()


# ---------------------------------------------------------------------------
# Story 4: Lore wiring, upvote/downvote dispatch, lazy import
# ---------------------------------------------------------------------------

class TestLoreRemoteInit:
    def test_lore_remote_store_init(self):
        from lore.lore import Lore
        with patch("lore.store.http.HttpStore._check_health"):
            lore = Lore(
                store="remote",
                api_url="http://localhost:8765",
                api_key="lore_sk_test",
            )
        from lore.store.http import HttpStore
        assert isinstance(lore._store, HttpStore)
        lore.close()

    def test_lore_remote_store_env_fallback(self, monkeypatch):
        monkeypatch.setenv("LORE_API_URL", "http://env:9999")
        monkeypatch.setenv("LORE_API_KEY", "lore_sk_env")
        from lore.lore import Lore
        with patch("lore.store.http.HttpStore._check_health"):
            lore = Lore(store="remote")
        from lore.store.http import HttpStore
        assert isinstance(lore._store, HttpStore)
        lore.close()

    def test_default_store_unchanged(self):
        import os
        import tempfile

        from lore.lore import Lore
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "test.db")
            lore = Lore(db_path=db)
            from lore.store.sqlite import SqliteStore
            assert isinstance(lore._store, SqliteStore)
            lore.close()


class TestUpvoteDownvoteDispatch:
    def test_upvote_uses_atomic_when_available(self):
        store = _make_store()
        store.upvote = MagicMock()
        from lore.lore import Lore
        with patch.object(Lore, "__init__", lambda self, **kw: None):
            lore = Lore.__new__(Lore)
            lore._store = store
            lore.upvote("mem-1")
            store.upvote.assert_called_once_with("mem-1")
        store.close()

    def test_downvote_uses_atomic_when_available(self):
        store = _make_store()
        store.downvote = MagicMock()
        from lore.lore import Lore
        with patch.object(Lore, "__init__", lambda self, **kw: None):
            lore = Lore.__new__(Lore)
            lore._store = store
            lore.downvote("mem-1")
            store.downvote.assert_called_once_with("mem-1")
        store.close()

    def test_upvote_falls_back_for_stores_without_method(self):
        from lore.lore import Lore
        from lore.store.memory import MemoryStore
        from lore.types import Memory

        mem_store = MemoryStore()
        mem = Memory(
            id="m1", content="test", created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        mem_store.save(mem)
        assert not hasattr(mem_store, 'upvote')

        with patch.object(Lore, "__init__", lambda self, **kw: None):
            lore = Lore.__new__(Lore)
            lore._store = mem_store
            lore.upvote("m1")
        updated = mem_store.get("m1")
        assert updated.upvotes == 1


class TestHttpStoreVoteMethods:
    def test_upvote_sends_patch(self):
        store = _make_store()
        store._client.request = MagicMock(
            return_value=_mock_response(200, json_data={
                "id": "x", "problem": "p", "resolution": "p", "meta": {},
            })
        )
        store.upvote("x")
        call_args = store._client.request.call_args
        assert call_args[0] == ("PATCH", "/v1/lessons/x")
        assert call_args[1]["json"] == {"upvotes": "+1"}
        store.close()

    def test_downvote_sends_patch(self):
        store = _make_store()
        store._client.request = MagicMock(
            return_value=_mock_response(200, json_data={
                "id": "x", "problem": "p", "resolution": "p", "meta": {},
            })
        )
        store.downvote("x")
        call_args = store._client.request.call_args
        assert call_args[1]["json"] == {"downvotes": "+1"}
        store.close()

    def test_upvote_not_found_raises(self):
        store = _make_store()
        store._client.request = MagicMock(return_value=_mock_response(404))
        from lore.exceptions import MemoryNotFoundError
        with pytest.raises(MemoryNotFoundError):
            store.upvote("gone")
        store.close()


class TestLazyImport:
    def test_import_httpstore_from_store_package(self):
        from lore.store import HttpStore
        assert HttpStore is not None

    def test_bad_attr_raises(self):
        with pytest.raises(AttributeError):
            from lore import store
            store.NonExistentThing


# ---------------------------------------------------------------------------
# Story 5: MCP server _get_lore() configuration tests
_has_mcp = pytest.importorskip("mcp", reason="mcp not installed")

# ---------------------------------------------------------------------------

class TestMcpGetLore:
    def setup_method(self):
        import lore.mcp.server as srv
        srv._lore = None

    def test_remote_store_from_env(self, monkeypatch):
        monkeypatch.setenv("LORE_STORE", "remote")
        monkeypatch.setenv("LORE_API_URL", "http://test:8765")
        monkeypatch.setenv("LORE_API_KEY", "lore_sk_test")
        monkeypatch.setenv("LORE_PROJECT", "myproj")

        import lore.mcp.server as srv
        with patch("lore.store.http.HttpStore._check_health"):
            lore = srv._get_lore()

        from lore.store.http import HttpStore
        assert isinstance(lore._store, HttpStore)
        assert lore.project == "myproj"
        srv._lore = None

    def test_local_store_default(self, monkeypatch):
        monkeypatch.delenv("LORE_STORE", raising=False)
        import lore.mcp.server as srv
        lore = srv._get_lore()
        from lore.store.sqlite import SqliteStore
        assert isinstance(lore._store, SqliteStore)
        srv._lore = None

    def test_invalid_store_type_raises(self, monkeypatch):
        monkeypatch.setenv("LORE_STORE", "invalid")
        import lore.mcp.server as srv
        with pytest.raises(ValueError, match="Invalid LORE_STORE"):
            srv._get_lore()
        srv._lore = None

    def test_project_works_with_remote(self, monkeypatch):
        monkeypatch.setenv("LORE_STORE", "remote")
        monkeypatch.setenv("LORE_API_URL", "http://test:8765")
        monkeypatch.setenv("LORE_API_KEY", "lore_sk_test")
        monkeypatch.setenv("LORE_PROJECT", "special")

        import lore.mcp.server as srv
        with patch("lore.store.http.HttpStore._check_health"):
            lore = srv._get_lore()
        assert lore.project == "special"
        srv._lore = None
