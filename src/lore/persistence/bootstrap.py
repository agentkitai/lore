"""Solo-mode bootstrap: seed an empty SQLite DB with org/workspace/api-key.

Phase 3J of the SQLite solo-mode design (decisions 1-2 in the spec). When
``SqliteStore.open()`` is called against a fresh DB, this module:

1. Detects an empty install (no active root API keys).
2. Inserts ``orgs(id='solo')`` and ``workspaces(id='solo', slug='solo')``.
3. Generates a ``lore_sk_<32 hex>`` raw key, stores its sha256 hash, writes
   the raw key to ``~/.lore/key.txt`` with mode ``0600``.

Idempotent: subsequent opens see a populated ``api_keys`` table and skip
bootstrap. In-memory DBs (``sqlite:///:memory:``) skip bootstrap entirely
by default because they're test-only — pass ``force_for_memory=True`` to
override (Phase 4A: AsyncLore needs the org row even for ``:memory:``).

Spec: docs/superpowers/specs/2026-05-05-sqlite-solo-mode-design.md
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

from lore.persistence.types import NewApiKey

if TYPE_CHECKING:  # pragma: no cover
    from lore.persistence.sqlite import SqliteStore

logger = logging.getLogger(__name__)

SOLO_ORG_ID = "solo"
SOLO_ORG_NAME = "Solo"
SOLO_WORKSPACE_ID = "solo"
SOLO_WORKSPACE_SLUG = "solo"
SOLO_WORKSPACE_NAME = "Solo"

DEFAULT_KEY_PATH = Path("~/.lore/key.txt")

# Sentinel: ``key_path`` is a 3-state knob — ``_DEFAULT`` (write to
# ``~/.lore/key.txt``), an explicit ``Path`` (write there), or ``None``
# (skip writing the key file entirely; used by ``:memory:`` runs).
_DEFAULT = object()


def _generate_raw_key() -> str:
    """Mint a fresh ``lore_sk_<hex>`` API key.

    32 bytes of randomness yields 64 hex chars; the ``lore_sk_`` prefix
    gives the auth middleware a fast-path discriminator (see ``lore.server.auth``).
    """
    return "lore_sk_" + secrets.token_hex(32)


async def bootstrap_solo_if_empty(
    store: "SqliteStore",
    *,
    key_path: Union[Path, None, object] = _DEFAULT,
    force_for_memory: bool = False,
) -> Optional[str]:
    """Seed the solo org + first API key when the DB is empty.

    Returns the raw API key string when a bootstrap was performed, ``None``
    otherwise (idempotent). When ``key_path`` is a ``Path`` (default:
    ``~/.lore/key.txt``) the raw key is also written there with mode
    ``0600``. Passing ``key_path=None`` skips writing the key file entirely
    (useful for ``:memory:`` test runs that should leave no on-disk
    artifacts).

    Args:
        store: An opened ``SqliteStore`` whose schema migrations have already
            been applied.
        key_path: Where to write the raw key.

            * Omitted / sentinel → ``~/.lore/key.txt`` (the historical default).
            * Explicit ``Path`` → write there with mode ``0600``.
            * ``None`` → do not write the key to disk at all; only the
              hash is persisted in the DB.
        force_for_memory: When ``True``, run bootstrap even for in-memory
            DBs (``store._db_path == ":memory:"``). Defaults to ``False`` so
            test fixtures that share the historical "skip :memory:" contract
            stay green; set this from the embedded API path
            (``AsyncLore``) where the org row is required.

    Skip conditions:
        * The DB is in-memory **and** ``force_for_memory`` is ``False``.
        * ``count_active_root_keys('solo') > 0`` already.
    """
    if getattr(store, "_db_path", None) == ":memory:" and not force_for_memory:
        logger.debug("bootstrap_solo_if_empty: skipping in-memory DB")
        return None

    existing = await store.count_active_root_keys(SOLO_ORG_ID)
    if existing > 0:
        logger.debug(
            "bootstrap_solo_if_empty: solo org already has %d active root keys; "
            "skipping bootstrap",
            existing,
        )
        return None

    # Defensive self-heal: if the DB is empty BUT ~/.lore/key.txt already
    # exists with non-empty contents, ADOPT that key instead of generating
    # a new one. This recovers from the "DB got rebuilt while key.txt was
    # left behind" failure mode without overwriting the user's stable key.
    # If `key_path=None` (in-memory tests), this branch is skipped.
    adopted_key: Optional[str] = None
    if key_path is not None:
        candidate = (
            DEFAULT_KEY_PATH if key_path is _DEFAULT else key_path  # type: ignore[assignment]
        )
        candidate = Path(candidate).expanduser()
        if candidate.exists():
            try:
                content = candidate.read_text().strip()
            except OSError:
                content = ""
            if content:
                adopted_key = content
                logger.info(
                    "bootstrap_solo_if_empty: adopting existing key at %s "
                    "(DB had no root keys)", candidate,
                )

    if adopted_key:
        raw_key = adopted_key
    else:
        raw_key = _generate_raw_key()
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    key_prefix = raw_key[:12]  # "lore_sk_" + 4 hex chars

    conn = store._conn
    if conn is None:  # pragma: no cover - defensive
        from lore.persistence.exceptions import StoreError

        raise StoreError("bootstrap_solo_if_empty: SqliteStore is closed")

    # Idempotent INSERTs for org + workspace (the slug-uniqueness on
    # workspaces is enforced by the schema's UNIQUE(org_id, slug)).
    await conn.execute(
        "INSERT OR IGNORE INTO orgs (id, name) VALUES (?, ?)",
        (SOLO_ORG_ID, SOLO_ORG_NAME),
    )
    await conn.execute(
        "INSERT OR IGNORE INTO workspaces (id, org_id, name, slug) "
        "VALUES (?, ?, ?, ?)",
        (
            SOLO_WORKSPACE_ID,
            SOLO_ORG_ID,
            SOLO_WORKSPACE_NAME,
            SOLO_WORKSPACE_SLUG,
        ),
    )
    await conn.commit()

    # Use the typed Store API for the API key so the row layout stays in
    # sync with future schema changes.
    await store.create_api_key(
        NewApiKey(
            org_id=SOLO_ORG_ID,
            name="solo-root",
            key_hash=key_hash,
            key_prefix=key_prefix,
            project=None,
            is_root=True,
            workspace_id=SOLO_WORKSPACE_ID,
        )
    )

    # Resolve the key-write destination. ``None`` means "do not persist"
    # (in-memory test runs). The sentinel falls back to the default path.
    if key_path is None:
        logger.info(
            "bootstrap_solo_if_empty: created solo org + root API key (key file skipped)"
        )
        return raw_key

    # If we adopted an existing key file, the file already holds the right
    # value — don't rewrite it (and don't risk a chmod race).
    if adopted_key:
        return raw_key

    target_path: Path = (
        DEFAULT_KEY_PATH if key_path is _DEFAULT else key_path  # type: ignore[assignment]
    )
    target = target_path.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(raw_key + "\n")
    try:
        os.chmod(target, 0o600)
    except OSError as exc:  # pragma: no cover - depends on FS
        logger.warning(
            "bootstrap_solo_if_empty: chmod 0600 failed on %s: %s", target, exc
        )

    logger.info(
        "bootstrap_solo_if_empty: created solo org + root API key (written to %s)",
        target,
    )
    return raw_key
