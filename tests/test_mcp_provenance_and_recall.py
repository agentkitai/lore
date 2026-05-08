"""Audit-fix tests for the MCP layer (May 2026).

Covers three concrete behaviors added to address gaps surfaced by the
provenance / fallback audit:

  * ``consolidate_memories`` posts the expected body to
    ``/v1/memories/consolidate`` and returns the new id +
    superseded_count parsed from the route response.
  * ``provenance`` formats sources / chain / metadata_sources from
    ``GET /v1/memories/{id}/provenance``.
  * ``recall`` no longer returns a flat "No relevant memories found"
    sentence on empty result — it now lists concrete fallback tools so
    the consumer can act instead of giving up.

The MCP tools call ``_temporal_request`` (and ``lore.recall`` for the
recall path), so each test mocks that single seam rather than spinning
up a TestClient.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

mcp = pytest.importorskip("mcp", reason="mcp not installed")  # noqa: F841

from lore import Lore  # noqa: E402
from lore.store.memory import MemoryStore  # noqa: E402


def _stub_embed(text: str):
    return [0.0] * 384


def _make_lore() -> Lore:
    return Lore(store=MemoryStore(), embedding_fn=_stub_embed)


# ── consolidate_memories ────────────────────────────────────────────


class TestConsolidateMemoriesTool:
    def test_posts_expected_body_and_parses_response(self):
        from lore.mcp.server import consolidate_memories

        captured = {}

        def fake_request(method, path, **kwargs):
            captured["method"] = method
            captured["path"] = path
            captured["json"] = kwargs.get("json")
            return {"id": "m-merged-1", "superseded_count": 2}

        with patch("lore.mcp.server._temporal_request", side_effect=fake_request):
            out = consolidate_memories(
                source_ids=["a", "b"],
                content="merged narrative",
                type="lesson",
                reason="merged near-duplicates",
            )

        assert captured["method"] == "POST"
        assert captured["path"] == "/v1/memories/consolidate"
        body = captured["json"]
        assert body["source_ids"] == ["a", "b"]
        assert body["content"] == "merged narrative"
        assert body["type"] == "lesson"
        assert body["reason"] == "merged near-duplicates"
        # context / tags / project / scope are omitted when caller didn't pass them
        assert "context" not in body
        assert "tags" not in body
        # Response surface is JSON with id + superseded_count.
        import json

        parsed = json.loads(out)
        assert parsed["id"] == "m-merged-1"
        assert parsed["superseded_count"] == 2

    def test_includes_optional_fields_when_set(self):
        from lore.mcp.server import consolidate_memories

        captured = {}

        def fake_request(method, path, **kwargs):
            captured["json"] = kwargs.get("json")
            return {"id": "m-1", "superseded_count": 1}

        with patch("lore.mcp.server._temporal_request", side_effect=fake_request):
            consolidate_memories(
                source_ids=["a"],
                content="x",
                context="ctx",
                tags=["t1"],
                project="lore",
                scope="global",
            )

        body = captured["json"]
        assert body["context"] == "ctx"
        assert body["tags"] == ["t1"]
        assert body["project"] == "lore"
        assert body["scope"] == "global"

    def test_propagates_failure_string(self):
        from lore.mcp.server import consolidate_memories

        def fake_request(method, path, **kwargs):
            raise RuntimeError("boom")

        with patch("lore.mcp.server._temporal_request", side_effect=fake_request):
            out = consolidate_memories(source_ids=["a"], content="x")
        assert out.startswith("Failed to consolidate memories:")
        assert "boom" in out


# ── provenance ──────────────────────────────────────────────────────


class TestProvenanceTool:
    def test_formats_sources_chain_and_metadata(self):
        from lore.mcp.server import provenance

        payload = {
            "memory_id": "m-merged",
            "sources": [
                {
                    "memory_id": "a",
                    "superseded_by": "m-merged",
                    "reason": "merge a",
                    "ts": "2026-05-08T00:00:00Z",
                    "agent": "api",
                }
            ],
            "chain": [
                {
                    "memory_id": "m-merged",
                    "superseded_by": "m-newer",
                    "reason": "later replaced",
                    "ts": "2026-06-01T00:00:00Z",
                    "agent": "api",
                }
            ],
            "metadata_sources": ["legacy-x"],
        }

        with patch("lore.mcp.server._temporal_request", return_value=payload):
            out = provenance("m-merged")

        assert "Provenance for m-merged" in out
        assert "Consolidated from 1 source(s)" in out
        assert "merge a" in out
        assert "Legacy meta.consolidated_from" in out
        assert "legacy-x" in out
        assert "Own supersession chain" in out
        assert "m-newer" in out

    def test_empty_provenance(self):
        from lore.mcp.server import provenance

        with patch(
            "lore.mcp.server._temporal_request",
            return_value={
                "memory_id": "m1",
                "sources": [],
                "chain": [],
                "metadata_sources": [],
            },
        ):
            out = provenance("m1")
        assert "no provenance history" in out


# ── recall: empty-result fallback hints ─────────────────────────────


class TestRecallEmptyMessage:
    def test_empty_result_lists_concrete_fallback_tools(self):
        """Audit fix (Gap 2): silence on empty recall must point the agent
        at other retrieval surfaces instead of just saying 'try a
        different query'."""
        from lore.mcp.server import recall

        lore = _make_lore()  # MemoryStore is empty → recall returns []
        with patch("lore.mcp.server._get_lore", return_value=lore):
            out = recall("anything totally novel")

        # Old wording stayed (substring) so the existing test_recall_empty
        # still passes; new wording adds concrete next steps.
        assert "No relevant memories" in out
        assert "list_memories" in out
        assert "recent_activity" in out
        assert "topics" in out
        assert "search" in out

    def test_empty_result_echoes_active_filters(self):
        """When filters are applied, the empty message should say so —
        a near-miss with the wrong type filter is the most common cause
        of false-empty recall."""
        from lore.mcp.server import recall

        lore = _make_lore()
        with patch("lore.mcp.server._get_lore", return_value=lore):
            out = recall(
                "anything",
                type="lesson",
                tags=["unmatched"],
            )

        assert "type='lesson'" in out
        assert "unmatched" in out
