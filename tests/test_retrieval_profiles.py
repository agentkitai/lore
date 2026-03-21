"""Tests for Retrieval Profiles (F4)."""

from __future__ import annotations


class TestProfileCache:
    def test_get_cached_returns_none_when_empty(self):
        from lore.server.routes.profiles import _get_cached_profile, _profile_cache
        _profile_cache.clear()
        assert _get_cached_profile("nonexistent") is None

    def test_set_and_get_cached(self):
        from lore.server.routes.profiles import _get_cached_profile, _profile_cache, _set_cached_profile
        _profile_cache.clear()
        profile = {"name": "test", "semantic_weight": 1.0}
        _set_cached_profile("org:test", profile)
        cached = _get_cached_profile("org:test")
        assert cached is not None
        assert cached["name"] == "test"

    def test_cache_ttl_expires(self):
        import time as _time

        from lore.server.routes.profiles import (
            _PROFILE_CACHE_TTL,
            _get_cached_profile,
            _profile_cache,
            _set_cached_profile,
        )
        _profile_cache.clear()
        _set_cached_profile("org:expire", {"name": "expire"})
        # Manually expire the entry
        key = "org:expire"
        old_val, _ = _profile_cache[key]
        _profile_cache[key] = (old_val, _time.monotonic() - _PROFILE_CACHE_TTL - 1)
        assert _get_cached_profile(key) is None


class TestProfileModels:
    def test_create_request_defaults(self):
        from lore.server.routes.profiles import ProfileCreateRequest
        req = ProfileCreateRequest(name="test")
        assert req.semantic_weight == 1.0
        assert req.graph_weight == 1.0
        assert req.recency_bias == 30.0
        assert req.min_score == 0.3
        assert req.max_results == 10

    def test_response_model(self):
        from lore.server.routes.profiles import ProfileResponse
        resp = ProfileResponse(
            id="test-id",
            org_id="org-1",
            name="fast-coding",
            semantic_weight=1.0,
            graph_weight=0.5,
            recency_bias=7.0,
            min_score=0.4,
            max_results=10,
            is_preset=False,
        )
        assert resp.name == "fast-coding"
        assert resp.recency_bias == 7.0
