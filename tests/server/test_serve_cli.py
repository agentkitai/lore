"""Phase 3J: ``lore serve`` bind-host policy tests.

Solo mode binds to ``127.0.0.1`` by default. Binding to ``0.0.0.0`` (or
any non-loopback address) without ``--require-auth`` raises
``InsecureBindError`` before uvicorn starts.
"""

from __future__ import annotations

import argparse

import pytest

from lore.cli.commands.server import _is_loopback_host, cmd_serve
from lore.persistence.exceptions import InsecureBindError


def _make_args(host: str, *, require_auth: bool = False, port: int = 8765) -> argparse.Namespace:
    return argparse.Namespace(host=host, port=port, require_auth=require_auth)


def test_is_loopback_host_classifies():
    assert _is_loopback_host("127.0.0.1")
    assert _is_loopback_host("127.0.0.42")
    assert _is_loopback_host("::1")
    assert _is_loopback_host("localhost")
    assert not _is_loopback_host("0.0.0.0")
    assert not _is_loopback_host("10.0.0.1")
    assert not _is_loopback_host("example.com")
    assert not _is_loopback_host("")


def test_bind_0_0_0_0_without_require_auth_refuses(monkeypatch):
    """Refuse to bind to 0.0.0.0 unless --require-auth is set."""
    # Patch uvicorn.run so an accidental success would still fail loudly.
    # cmd_serve raises BEFORE reaching uvicorn, so this is belt-and-braces.
    args = _make_args("0.0.0.0", require_auth=False)
    with pytest.raises(InsecureBindError):
        cmd_serve(args)


def test_bind_localhost_works_without_require_auth(monkeypatch):
    """Loopback bind addresses don't need --require-auth."""
    called = {}

    class _FakeUvicorn:
        @staticmethod
        def run(app, host, port):
            called["app"] = app
            called["host"] = host
            called["port"] = port

    # Replace the imported uvicorn module so cmd_serve uses our fake.
    monkeypatch.setitem(__import__("sys").modules, "uvicorn", _FakeUvicorn)

    args = _make_args("127.0.0.1", require_auth=False, port=18765)
    cmd_serve(args)
    assert called["host"] == "127.0.0.1"
    assert called["port"] == 18765


def test_bind_0_0_0_0_with_require_auth_allowed(monkeypatch):
    """0.0.0.0 + --require-auth bypasses the InsecureBindError check."""
    called = {}

    class _FakeUvicorn:
        @staticmethod
        def run(app, host, port):
            called["host"] = host
            called["port"] = port

    monkeypatch.setitem(__import__("sys").modules, "uvicorn", _FakeUvicorn)

    args = _make_args("0.0.0.0", require_auth=True, port=18766)
    cmd_serve(args)
    assert called["host"] == "0.0.0.0"


def test_bind_non_loopback_hostname_refused(monkeypatch):
    """A non-loopback hostname (e.g. ``example.com``) is also refused."""
    args = _make_args("example.com", require_auth=False)
    with pytest.raises(InsecureBindError):
        cmd_serve(args)


def test_default_serve_host_is_loopback():
    """The CLI parser default for --host is the loopback address."""
    from lore.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["serve"])
    assert args.host == "127.0.0.1"
    assert args.require_auth is False
