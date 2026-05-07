"""Phase 6G helpers: project resolution + ``<private>`` tag stripping.

These helpers are deliberately split out from ``capture.py`` so they can
be imported by hook scripts (``lore-capture-prompt.sh`` shells out to a
small Python entrypoint) and by other capture-adjacent code paths
without dragging the whole capture-extract subagent machinery along.

Two surfaces:

* ``resolve_project(cwd)`` — derives a stable project identifier from
  git context. Remote URL is preferred (stable cross-machine,
  cross-worktree); falls back to the basename of ``--git-common-dir``
  for repos without a remote. Cached per-cwd because the caller (the
  capture pipeline) can hit it once per emitted observation.

* ``strip_private(text)`` — strips ``<private>...</private>`` blocks
  before any user content reaches the buffer. Fails closed: an
  unbalanced opening tag strips to end-of-string rather than letting
  the unredacted tail leak out.
"""

from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Optional

# ── Project resolution ────────────────────────────────────────────────


_GIT_TIMEOUT_SECONDS = 2.0


def _normalize_remote_url(url: str) -> Optional[str]:
    """Return a stable ``host/user/repo`` form for a git remote URL.

    Examples::

        https://github.com/user/repo.git  → "github.com/user/repo"
        https://GitHub.com/user/repo      → "github.com/user/repo"
        git@github.com:user/repo.git      → "github.com/user/repo"
        ssh://git@gitlab.com/group/repo   → "gitlab.com/group/repo"

    Hosts are lowercased so case-only differences don't shard projects.
    Repo paths preserve their original case (some hosts are case-sensitive
    for the path component). The trailing ``.git`` suffix and any leading
    or trailing slashes are trimmed. Returns ``None`` if the input doesn't
    parse to a recognizable host + path.
    """
    if not url:
        return None
    raw = url.strip()
    if not raw:
        return None

    # Form 1: scp-like SSH — ``git@host:path``. Distinguished from a real
    # URL by the absence of "://" and the presence of "@host:".
    if "://" not in raw and "@" in raw and ":" in raw:
        # Strip optional ``user@`` prefix.
        _, _, after_at = raw.rpartition("@")
        host, _, path = after_at.partition(":")
        if not host or not path:
            return None
        host = host.lower()
        path = path.strip("/")
        if path.endswith(".git"):
            path = path[: -len(".git")]
        if not path:
            return None
        return f"{host}/{path}"

    # Form 2: scheme://[user@]host[:port]/path — covers https, ssh, git.
    m = re.match(
        r"^[a-zA-Z][a-zA-Z0-9+.-]*://(?:[^@/]+@)?([^:/]+)(?::\d+)?/(.+)$",
        raw,
    )
    if m:
        host = m.group(1).lower()
        path = m.group(2).strip("/")
        if path.endswith(".git"):
            path = path[: -len(".git")]
        if not path:
            return None
        return f"{host}/{path}"

    return None


def _git_invoke(cwd: Path, *args: str) -> Optional[str]:
    """Run ``git -C <cwd> <args>``; return stripped stdout or ``None``.

    Swallows the usual triad of "not a repo / git missing / hung" errors
    so callers can treat missing git context the same as "no git".
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    out = result.stdout.strip()
    return out or None


@lru_cache(maxsize=64)
def _resolve_project_cached(cwd_str: str) -> Optional[str]:
    """Inner cached implementation; key is the path string for hashability."""
    cwd = Path(cwd_str)
    url = _git_invoke(cwd, "config", "--get", "remote.origin.url")
    if url:
        normalized = _normalize_remote_url(url)
        if normalized:
            return normalized
    common = _git_invoke(cwd, "rev-parse", "--git-common-dir")
    if common:
        common_path = Path(common)
        if not common_path.is_absolute():
            common_path = (cwd / common_path).resolve()
        else:
            common_path = common_path.resolve()
        # ``--git-common-dir`` points at ``.git`` (or ``.git/worktrees/...``
        # for a linked worktree). Worktree resolution: ``--git-common-dir``
        # always returns the *primary* repo's ``.git``, so its parent is
        # the primary repo's working directory regardless of which
        # worktree we asked from. That gives us a stable group key.
        return common_path.parent.name or None
    return None


def resolve_project(cwd: Path) -> Optional[str]:
    """Return the stable project identifier for ``cwd``, or ``None``.

    Strategy:
    1. If ``cwd`` is inside a git repo with a remote, use the
       normalized remote URL (``github.com/user/repo``) — stable across
       machines and worktrees, and groups multiple clones together.
    2. Else, if ``cwd`` is in a git repo without a remote, use the
       basename of ``git rev-parse --git-common-dir``'s parent.
       Worktrees of the same primary repo all collapse to the same key.
    3. Else, return ``None`` — caller stores the observation with
       ``project=NULL`` and it lives only under ``scope='all'`` recall.

    Cached per-cwd via ``functools.lru_cache(maxsize=64)``. Tests can
    clear the cache with ``resolve_project.cache_clear()``.
    """
    return _resolve_project_cached(str(cwd))


# Expose ``cache_clear`` on the public name so tests can reset between
# fixtures without poking at the underscore-prefixed inner.
resolve_project.cache_clear = _resolve_project_cached.cache_clear  # type: ignore[attr-defined]


# ── ``<private>`` stripping ──────────────────────────────────────────


_PRIVATE_BLOCK_RE = re.compile(
    r"<private>.*?</private>",
    re.DOTALL | re.IGNORECASE,
)
# Fail-closed: an unbalanced opening tag strips from ``<private>`` to EOS.
_PRIVATE_TAIL_RE = re.compile(
    r"<private>.*$",
    re.DOTALL | re.IGNORECASE,
)


def strip_private(text: str) -> str:
    """Remove ``<private>...</private>`` blocks from ``text``.

    Two passes:
    1. Non-greedy DOTALL strip of *balanced* ``<private>...</private>``
       blocks (case-insensitive, multi-line OK).
    2. If an unbalanced opening tag survives the first pass, strip from
       it to end-of-string. This is the "fail closed" rule — if we can't
       see the closing tag, we assume the rest of the input is sensitive.

    Idempotent. Returns ``text`` unchanged if no ``<private>`` markers are
    present. Empty blocks (``<private></private>``) collapse to an empty
    string, leaving surrounding text intact.
    """
    if not text:
        return text
    cleaned = _PRIVATE_BLOCK_RE.sub("", text)
    cleaned = _PRIVATE_TAIL_RE.sub("", cleaned)
    return cleaned
