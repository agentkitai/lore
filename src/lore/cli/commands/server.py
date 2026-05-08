"""Server commands — serve, mcp, ui."""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys

from lore.persistence.exceptions import InsecureBindError

DEFAULT_API_URL = "http://127.0.0.1:8765"
# Module-level so tests can monkeypatch a tmp file without touching $HOME.
DEFAULT_KEY_PATH = os.path.expanduser("~/.lore/key.txt")


def _is_loopback_host(host: str) -> bool:
    """Return True if ``host`` resolves to a loopback (127.x / ::1 / localhost).

    A trailing port (``host:port``) is *not* expected here — the CLI splits
    those upstream. Hostnames that don't parse as an IP literal default to
    treating ``localhost`` as loopback and everything else as non-loopback.
    """
    if not host:
        return False
    if host.lower() in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def cmd_serve(args: argparse.Namespace) -> None:
    try:
        import uvicorn
    except ImportError:
        print(
            "Error: Server dependencies not installed.\n"
            "Install with: pip install lore-sdk[server]",
            file=sys.stderr,
        )
        sys.exit(1)
    port = args.port or int(os.environ.get("LORE_PORT", "8765"))
    host = args.host
    require_auth = bool(getattr(args, "require_auth", False))
    if not _is_loopback_host(host) and not require_auth:
        raise InsecureBindError(
            f"Cannot bind to {host!r} without --require-auth. Solo mode "
            "binds to 127.0.0.1 by default; pass --require-auth to "
            "acknowledge that authentication is enforced before exposing "
            "Lore on a non-loopback address."
        )

    # Lazy-server idle-timeout: prefer --idle-timeout, fall back to env
    # var, default 0 (disabled). Push the resolved value back into
    # LORE_IDLE_TIMEOUT so the FastAPI app (which reads the env at
    # lifespan-start time) sees a single source of truth even when the
    # flag was the one that supplied it.
    idle_timeout_arg = getattr(args, "idle_timeout", None)
    if idle_timeout_arg is None:
        idle_timeout = int(os.environ.get("LORE_IDLE_TIMEOUT", "0") or "0")
    else:
        idle_timeout = int(idle_timeout_arg)
    if idle_timeout < 0:
        idle_timeout = 0
    os.environ["LORE_IDLE_TIMEOUT"] = str(idle_timeout)

    if idle_timeout > 0:
        print(
            f"Starting Lore server on {host}:{port} "
            f"(idle-timeout: {idle_timeout}s)"
        )
    else:
        print(f"Starting Lore server on {host}:{port}")
    uvicorn.run("lore.server.app:app", host=host, port=port)


def _resolve_mcp_env() -> None:
    """Populate ``LORE_API_URL`` / ``LORE_API_KEY`` from sane defaults.

    Claude Code launches ``lore mcp`` with whatever environment the
    parent process has — typically nothing lore-specific. The MCP store
    constructor raises ``api_url is required`` when both are missing.
    Default the URL to the loopback solo-mode server and read the key
    from ``~/.lore/key.txt`` (the bootstrap target) so the bridge works
    out of the box.
    """
    if not os.environ.get("LORE_API_URL"):
        os.environ["LORE_API_URL"] = DEFAULT_API_URL
    if not os.environ.get("LORE_API_KEY"):
        try:
            with open(DEFAULT_KEY_PATH, encoding="utf-8") as f:
                key = f.read().strip()
        except OSError:
            return
        if key:
            os.environ["LORE_API_KEY"] = key


def _health_ok(api_url: str, *, timeout: float = 0.5) -> bool:
    """Return True iff ``GET {api_url}/health`` returns 2xx within ``timeout``."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"{api_url.rstrip('/')}/health", timeout=timeout
        ) as r:
            return 200 <= getattr(r, "status", 200) < 300
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return False


def _lazy_start_server(
    api_url: str,
    *,
    idle_timeout: int = 3600,
    health_probe=None,
    spawn_fn=None,
    sleep_fn=None,
) -> bool:
    """Probe ``/health``; if down, spawn ``lore serve`` and poll.

    Mirrors the bash ``_lore_ensure_server`` helper rendered into the
    auto-retrieval hooks, so a fresh ``lore mcp`` call brings the
    server up the same way a UserPromptSubmit hook would.

    ``LORE_NO_AUTOSTART=true`` skips the spawn for users who want to
    manage the server themselves. Returns True iff the server is
    reachable when the function returns.
    """
    import shutil
    import subprocess
    import time

    probe = health_probe or (lambda: _health_ok(api_url))
    sleep = sleep_fn or time.sleep

    if probe():
        return True
    if os.environ.get("LORE_NO_AUTOSTART", "").lower() == "true":
        return False
    if spawn_fn is None and not shutil.which("lore"):
        return False

    if spawn_fn is None:

        def spawn_fn():
            log = open("/tmp/lore-serve.log", "ab")
            subprocess.Popen(
                [
                    "lore",
                    "serve",
                    "--port",
                    "8765",
                    "--idle-timeout",
                    str(idle_timeout),
                ],
                stdout=log,
                stderr=log,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )

    try:
        spawn_fn()
    except OSError:
        return False

    for _ in range(6):
        sleep(0.5)
        if probe():
            return True
    return False


def cmd_mcp(args: argparse.Namespace) -> None:
    try:
        from lore.mcp.server import run_server
    except ImportError:
        print(
            "Error: MCP dependencies not installed.\n"
            "Install with: pip install lore-sdk[mcp]",
            file=sys.stderr,
        )
        sys.exit(1)
    _resolve_mcp_env()
    idle_timeout = int(os.environ.get("LORE_IDLE_TIMEOUT") or "3600")
    _lazy_start_server(os.environ["LORE_API_URL"], idle_timeout=idle_timeout)
    run_server()


def cmd_ui(args: argparse.Namespace) -> None:
    """Open graph visualization UI in the browser."""
    import webbrowser

    host = getattr(args, "host", "localhost")
    port = getattr(args, "port", 8765)
    url = f"http://{host}:{port}/ui"

    print(f"Opening Lore Graph UI: {url}")
    if not getattr(args, "no_open", False):
        webbrowser.open(url)
