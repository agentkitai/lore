"""``AsyncLore`` — embedded async API for the Lore SDK.

Phase 4 of the SQLite-solo-mode design (spec section "Component 4"). Lets a
Python app embed Lore directly — no HTTP server, no Postgres dependency —
while still going through the same ``Store`` + ``services/*`` layers the
HTTP routes use. The class is deliberately thin: every method builds typed
params and forwards to the matching service function.

Usage::

    async with AsyncLore("sqlite:///~/.lore/lore.db") as lore:
        m = await lore.remember("Always retry with backoff")
        hits = await lore.recall("retry policy")

Phase 4A scope (this module): skeleton + lifecycle + a foundational subset
of methods (remember / recall / get / forget / list_memories). Phase 4B
fills in the remaining ~25 methods that mirror the sync ``Lore`` surface;
Phase 4C wires in the background workers (SLO, retention, ingest).

Spec: docs/superpowers/specs/2026-05-05-sqlite-solo-mode-design.md
"""

from __future__ import annotations

import inspect
import logging
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    List,
    Optional,
    Sequence,
    Union,
)

from lore.persistence import (
    ConfigError,
    Store,
    StoredMemory,
    make_store,
)
from lore.services import memories as memories_service
from lore.services import retrieve as retrieve_service

if TYPE_CHECKING:  # pragma: no cover
    from lore.embed.base import Embedder

logger = logging.getLogger(__name__)

# An embedding function may be sync (returns ``Sequence[float]``) or async
# (returns an awaitable). ``AsyncLore.remember`` handles both.
EmbeddingFn = Callable[[str], Union[Sequence[float], Awaitable[Sequence[float]]]]


def _default_embedder() -> "Embedder":
    """Build the default in-process embedder (LocalEmbedder, 384-dim).

    Lazy import: pulling in ``lore.embed.local`` triggers onnxruntime/
    tokenizers loads, which we want to defer past ``AsyncLore`` import.
    """
    from lore.embed.local import LocalEmbedder

    return LocalEmbedder()


async def _resolve_org_id(store: Store, requested: Optional[str]) -> str:
    """Resolve which ``org_id`` this AsyncLore instance should bind to.

    * ``requested`` — explicit override wins (validated against the DB).
    * Else for SQLite: default to ``"solo"`` (the Phase 3J bootstrap and
      the embedded-mode ``__aenter__`` both seed ``orgs(id='solo')``).
    * Postgres without ``requested``: take the first row of ``orgs``;
      raise :class:`ConfigError` if the table is empty (AsyncLore doesn't
      auto-create orgs against a multi-tenant deployment).

    SqliteStore and PostgresStore both surface their connection through
    ``store._conn`` (a property in SQLite's case, a bound conn in
    Postgres's). We dispatch on store type by class name to keep the
    SQL-dialect difference (``?`` vs ``$1``) explicit without importing
    both backends.
    """
    candidate = requested or "solo"
    cls_name = type(store).__name__

    if cls_name == "SqliteStore":
        conn = getattr(store, "_conn", None)
        if conn is None:
            raise ConfigError("AsyncLore: SqliteStore is not open")
        async with conn.execute(
            "SELECT id FROM orgs WHERE id = ?", (candidate,)
        ) as cur:
            row = await cur.fetchone()
        if row is not None:
            return candidate
        if requested is not None:
            raise ConfigError(f"AsyncLore: org_id={requested!r} not found in DB")
        raise ConfigError(
            "AsyncLore: no 'solo' org found; bootstrap may have failed"
        )

    if cls_name == "PostgresStore":
        acquire = getattr(store, "_acquire", None)
        if acquire is None:  # pragma: no cover - defensive
            raise ConfigError(
                "AsyncLore: PostgresStore is missing _acquire; cannot resolve org_id"
            )
        async with acquire() as conn:
            if requested is None:
                row = await conn.fetchrow(
                    "SELECT id FROM orgs ORDER BY created_at LIMIT 1"
                )
            else:
                row = await conn.fetchrow(
                    "SELECT id FROM orgs WHERE id = $1", requested
                )
        if row is not None:
            return row["id"]
        if requested is not None:
            raise ConfigError(f"AsyncLore: org_id={requested!r} not found in DB")
        raise ConfigError(
            "AsyncLore: no orgs found in Postgres DB. Run `lore bootstrap` "
            "or initialize the schema before opening AsyncLore."
        )

    # Unknown Store flavor (e.g. an in-memory test stub) — accept the
    # candidate as-is but warn so the caller can debug if needed.
    logger.warning(
        "AsyncLore: cannot validate org_id against Store of type %s; using %r",
        cls_name,
        candidate,
    )
    return candidate


class AsyncLore:
    """Embedded async API for Lore.

    Use as an async context manager::

        async with AsyncLore(database_url) as lore:
            await lore.remember("...")

    Parameters
    ----------
    database_url:
        ``sqlite:///path/to/file.db`` or ``sqlite:///:memory:`` for the
        embedded SQLite backend (auto-bootstrap on first open).
        ``postgresql://...`` for a managed Postgres+pgvector deployment;
        the schema/org/workspace must already exist.
    workspace:
        Workspace slug used for service calls that need a workspace
        context. Defaults to ``"solo"``. Phase 4A doesn't yet route this
        into individual service calls — most foundational methods operate
        at the org level. Stored on the instance for Phase 4B/4C use.
    api_key:
        Optional API-key string. The embedded API doesn't validate it
        (there's no auth middleware in-process); it's stored for callers
        that want to round-trip the same key into HTTP fallbacks.
    embed:
        Embedding function. May be sync (``str -> Sequence[float]``) or
        async (``str -> Awaitable[Sequence[float]]``). Defaults to the
        in-process :class:`~lore.embed.local.LocalEmbedder` (384-dim).
    org_id:
        Override the auto-resolved org. Defaults to ``"solo"`` for SQLite
        (created by the Phase 3J bootstrap); for Postgres the first row of
        the ``orgs`` table is used when this is ``None``.
    """

    # Phase 4A surface. Phase 4B will extend this list.
    __slots__ = (
        "_database_url",
        "_workspace",
        "_api_key",
        "_embed",
        "_requested_org_id",
        "_store",
        "_org_id",
        "_closed",
    )

    def __init__(
        self,
        database_url: str,
        *,
        workspace: str = "solo",
        api_key: Optional[str] = None,
        embed: Optional[EmbeddingFn] = None,
        org_id: Optional[str] = None,
    ) -> None:
        self._database_url = database_url
        self._workspace = workspace
        self._api_key = api_key
        self._embed = embed
        self._requested_org_id = org_id
        self._store: Optional[Store] = None
        self._org_id: Optional[str] = None
        self._closed = False

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def __aenter__(self) -> "AsyncLore":
        """Open the Store, ensure the org exists, return ``self``.

        For ``sqlite://`` URLs this triggers the Phase 3J bootstrap; if the
        URL is ``:memory:`` (or otherwise empty), AsyncLore re-runs
        ``bootstrap_solo_if_empty`` with ``force_for_memory=True`` so the
        embedded API always has a real org row to talk to.
        """
        store = await make_store(self._database_url)
        self._store = store

        # If we hit a SqliteStore against ``:memory:``, the factory's
        # auto-bootstrap was suppressed. Re-run it now with the embedded-
        # mode override so the org/workspace/api-key exist for service
        # calls. The key file is suppressed — in-memory runs leave no
        # on-disk artifacts.
        if getattr(store, "_db_path", None) == ":memory:":
            from lore.persistence.bootstrap import bootstrap_solo_if_empty

            await bootstrap_solo_if_empty(
                store, key_path=None, force_for_memory=True
            )

        try:
            self._org_id = await _resolve_org_id(store, self._requested_org_id)
        except Exception:
            # Best-effort cleanup so a bad org_id doesn't leak the Store.
            await self._safe_close_store()
            raise

        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._safe_close_store()
        self._closed = True

    async def _safe_close_store(self) -> None:
        store = self._store
        if store is None:
            return
        close = getattr(store, "close", None)
        if close is None:
            return
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except Exception:  # pragma: no cover - defensive
            logger.warning("AsyncLore: store.close() raised", exc_info=True)

    # ── Internal helpers ────────────────────────────────────────────────

    def _require_store(self) -> Store:
        if self._store is None or self._closed:
            raise RuntimeError(
                "AsyncLore: not opened. Use `async with AsyncLore(...) as lore:`."
            )
        return self._store

    @property
    def org_id(self) -> str:
        """The resolved org_id this AsyncLore is bound to."""
        if self._org_id is None:
            raise RuntimeError(
                "AsyncLore: org_id not resolved yet (call inside `async with`)."
            )
        return self._org_id

    @property
    def workspace(self) -> str:
        return self._workspace

    @property
    def store(self) -> Store:
        """The underlying Store. Exposed for advanced/Phase 4B+ use."""
        return self._require_store()

    async def _embed_text(self, text: str) -> List[float]:
        """Run the configured embedder. Lazy-instantiate the default."""
        embed_fn = self._embed
        if embed_fn is None:
            embedder = _default_embedder()
            return list(embedder.embed(text))
        result = embed_fn(text)
        if inspect.isawaitable(result):
            result = await result
        return list(result)

    # ── Foundational methods (Phase 4A) ─────────────────────────────────

    async def remember(
        self,
        content: str,
        *,
        project: Optional[str] = None,
        tags: Optional[Sequence[str]] = None,
        source: Optional[str] = None,
        embedding: Optional[Sequence[float]] = None,
        context: Optional[str] = None,
        confidence: float = 0.5,
        meta: Optional[dict[str, Any]] = None,
    ) -> StoredMemory:
        """Store a memory. Returns the persisted ``StoredMemory``.

        If ``embedding`` is omitted, the configured embedder is invoked on
        ``content``. Pass an explicit vector to skip the embedding step.
        """
        store = self._require_store()
        vec = list(embedding) if embedding is not None else await self._embed_text(content)
        return await memories_service.create_memory(
            store,
            org_id=self.org_id,
            content=content,
            embedding=vec,
            context=context,
            tags=tuple(tags or ()),
            confidence=confidence,
            source=source,
            project=project,
            meta=meta or {},
        )

    async def recall(
        self,
        query: str,
        *,
        k: int = 10,
        project: Optional[str] = None,
        min_score: float = 0.3,
        half_life_days: int = 30,
    ) -> List[StoredMemory]:
        """Vector-recall memories matching ``query``.

        Returns a list of ``ScoredMemory`` (a subclass of ``StoredMemory``
        carrying a ``.score`` attribute). Phase 4A uses the
        ``services.retrieve.retrieve`` helper but throws away the formatted
        block — the embedded API surfaces objects, not strings.
        """
        store = self._require_store()
        vec = await self._embed_text(query)
        result = await retrieve_service.retrieve(
            store,
            org_id=self.org_id,
            query_text=query,
            query_vec=vec,
            limit=k,
            min_score=min_score,
            project=project,
            format="raw",
            half_life_days=half_life_days,
        )
        return list(result.memories)

    async def get(self, memory_id: str) -> Optional[StoredMemory]:
        """Fetch a memory by id, or ``None`` if it doesn't exist."""
        store = self._require_store()
        return await memories_service.get_memory(store, self.org_id, memory_id)

    async def forget(self, memory_id: str) -> bool:
        """Delete a memory. Returns ``True`` if a row was removed."""
        store = self._require_store()
        return await memories_service.delete_memory(
            store, org_id=self.org_id, memory_id=memory_id
        )

    async def list_memories(
        self,
        *,
        project: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        tags: Optional[Sequence[str]] = None,
        include_expired: bool = False,
    ) -> List[StoredMemory]:
        """List memories for the bound org. Phase 4A: thin pass-through."""
        store = self._require_store()
        rows = await memories_service.list_memories(
            store,
            org_id=self.org_id,
            project=project,
            tags=tags,
            limit=limit,
            offset=offset,
            include_expired=include_expired,
        )
        return list(rows)
