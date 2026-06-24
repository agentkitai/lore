"""LORE_STORE=local wires the MCP server to the embedded SQLite server.

Fast wiring test — does NOT boot a real server. The end-to-end boot (uvicorn +
SqliteStore + key bootstrap) is exercised manually / by the server suite; here
we only pin the bootstrap logic in ``_get_lore()`` so the historical bug (the
``local`` branch building an HttpStore with no URL → crash on every tool call)
can't regress.
"""

from __future__ import annotations

import pytest


def test_local_mode_uses_embedded_server(monkeypatch):
    srv = pytest.importorskip("lore.mcp.server")

    monkeypatch.setenv("LORE_STORE", "local")
    monkeypatch.delenv("LORE_API_URL", raising=False)
    monkeypatch.delenv("LORE_API_KEY", raising=False)
    monkeypatch.setattr(srv, "_lore", None)

    started = {"called": False}

    def fake_start():
        started["called"] = True
        return "http://127.0.0.1:54321", "lore_sk_test"

    captured = {}

    def fake_lore(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(srv, "_start_embedded_server", fake_start)
    monkeypatch.setattr(srv, "Lore", fake_lore)

    srv._get_lore()

    assert started["called"], "local mode must start the embedded server"
    assert captured["store"] == "remote"
    assert captured["api_url"] == "http://127.0.0.1:54321"
    assert captured["api_key"] == "lore_sk_test"


def test_remote_mode_does_not_start_embedded_server(monkeypatch):
    srv = pytest.importorskip("lore.mcp.server")

    monkeypatch.setenv("LORE_STORE", "remote")
    monkeypatch.setenv("LORE_API_URL", "http://example:8765")
    monkeypatch.setenv("LORE_API_KEY", "lore_sk_remote")
    monkeypatch.setattr(srv, "_lore", None)

    def boom():
        raise AssertionError("remote mode must NOT start an embedded server")

    captured = {}

    def fake_lore(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(srv, "_start_embedded_server", boom)
    monkeypatch.setattr(srv, "Lore", fake_lore)

    srv._get_lore()

    assert captured["api_url"] == "http://example:8765"
    assert captured["api_key"] == "lore_sk_remote"
