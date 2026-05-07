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

Phase 4A: skeleton + lifecycle + a foundational subset of methods
(remember / recall / get / forget / list_memories).
Phase 4B: the remaining ~20 methods that mirror the sync ``Lore`` surface.
Phase 4C (this commit): background workers (retention, SLO, alerting,
ingest) start in ``__aenter__`` and stop cooperatively in ``__aexit__``;
``add_conversation`` enqueues onto the ingest queue. See
:mod:`lore._workers` for the worker classes.

Spec: docs/superpowers/specs/2026-05-05-sqlite-solo-mode-design.md
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Union,
)

from lore._workers import (
    AlertingWorker,
    IngestWorker,
    RetentionWorker,
    SloWorker,
)
from lore.persistence import (
    ConfigError,
    Store,
    StoredConversationJob,
    StoredMemory,
    make_store,
)
from lore.services import memories as memories_service
from lore.services import recent as recent_service
from lore.services import retrieve as retrieve_service
from lore.services import snapshots as snapshots_service
from lore.services.conversations import (
    create_job as conversations_create_job,
)
from lore.services.conversations import (
    get_job_status as conversations_get_job_status,
)
from lore.services.graph import entities as graph_entities_service
from lore.services.graph import review as graph_review_service

if TYPE_CHECKING:  # pragma: no cover
    from lore.classify.base import Classification
    from lore.embed.base import Embedder
    from lore.persistence import StoredEntity

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

    # Phase 4C surface includes the worker handles.
    __slots__ = (
        "_database_url",
        "_workspace",
        "_api_key",
        "_embed",
        "_requested_org_id",
        "_store",
        "_org_id",
        "_closed",
        "_auto_workers",
        "_retention_worker",
        "_slo_worker",
        "_ingest_worker",
        "_alerting_worker",
        "_ingest_queue",
        "_worker_tasks",
    )

    def __init__(
        self,
        database_url: str,
        *,
        workspace: str = "solo",
        api_key: Optional[str] = None,
        embed: Optional[EmbeddingFn] = None,
        org_id: Optional[str] = None,
        auto_workers: bool = True,
    ) -> None:
        self._database_url = database_url
        self._workspace = workspace
        self._api_key = api_key
        self._embed = embed
        self._requested_org_id = org_id
        self._store: Optional[Store] = None
        self._org_id: Optional[str] = None
        self._closed = False
        self._auto_workers = auto_workers
        self._retention_worker: Optional[RetentionWorker] = None
        self._slo_worker: Optional[SloWorker] = None
        self._ingest_worker: Optional[IngestWorker] = None
        self._alerting_worker: Optional[AlertingWorker] = None
        self._ingest_queue: Optional[asyncio.Queue[tuple[str, str]]] = None
        self._worker_tasks: List[asyncio.Task[None]] = []

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

        if self._auto_workers:
            self._start_workers()

        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # Stop the workers first so a pending tick can't fire against a
        # half-closed Store. Surface uncaught worker exceptions out of
        # __aexit__ per spec ("embedded mode propagates uncaught worker
        # exceptions out the __aexit__ boundary").
        first_worker_exc: Optional[BaseException] = None
        if self._worker_tasks:
            for task in self._worker_tasks:
                task.cancel()
            results = await asyncio.gather(
                *self._worker_tasks, return_exceptions=True,
            )
            for r in results:
                if isinstance(r, BaseException) and not isinstance(
                    r, asyncio.CancelledError,
                ):
                    logger.error(
                        "AsyncLore: worker task raised", exc_info=r,
                    )
                    if first_worker_exc is None:
                        first_worker_exc = r
            self._worker_tasks = []

        await self._safe_close_store()
        self._closed = True

        # Re-raise the first uncaught worker exception (if the user's
        # block didn't already raise) so shutdown failures are visible.
        if first_worker_exc is not None and exc is None:
            raise first_worker_exc

    def _start_workers(self) -> None:
        """Spawn the four background workers + the ingest queue.

        :class:`AlertingWorker` is event-driven (no tick), so it isn't
        added to ``_worker_tasks``; it's still wired into :class:`SloWorker`
        and exposed as :attr:`_alerting_worker` for direct dispatch.
        """
        store = self._require_store()
        self._alerting_worker = AlertingWorker(store)
        self._ingest_queue = asyncio.Queue()
        self._retention_worker = RetentionWorker(store)
        self._slo_worker = SloWorker(store, self._alerting_worker)
        self._ingest_worker = IngestWorker(store, self._ingest_queue)
        loop_workers = (
            self._retention_worker,
            self._slo_worker,
            self._ingest_worker,
        )
        self._worker_tasks = [
            asyncio.create_task(w.run_forever(), name=f"lore-{w.name}")
            for w in loop_workers
        ]
        # Stash the task reference on the worker so .stop() Just Works.
        for w, t in zip(loop_workers, self._worker_tasks):
            w._task = t

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

    # ── Phase 4B: snapshots ─────────────────────────────────────────────

    async def save_snapshot(
        self,
        content: str,
        *,
        title: Optional[str] = None,
        session_id: Optional[str] = None,
        tags: Optional[Sequence[str]] = None,
        project: Optional[str] = None,
    ) -> StoredMemory:
        """Persist a session snapshot as a tagged memory.

        Snapshots are stored verbatim with a placeholder zero-vector by the
        ``services.snapshots`` layer — they aren't recall targets, the
        intent is to round-trip a full session blob keyed by ``session_id``.
        """
        if not content or not content.strip():
            raise ValueError("content must be non-empty")
        store = self._require_store()
        return await snapshots_service.create_snapshot(
            store,
            org_id=self.org_id,
            content=content,
            title=title,
            session_id=session_id,
            tags=tags,
            project=project,
        )

    # ── Phase 4B: topics ────────────────────────────────────────────────

    async def list_topics(
        self,
        *,
        entity_type: Optional[str] = None,
        min_mentions: int = 3,
        limit: int = 50,
        project: Optional[str] = None,  # accepted for parity; unused in async path
    ) -> Sequence["StoredEntity"]:
        """List entities with mention_count >= ``min_mentions``.

        Returns ``StoredEntity`` rows ordered by mention_count DESC. The
        ``project`` arg is accepted for parity with the sync class but
        isn't filtered through here yet (matches the topics-dashboard
        service, which is org-scoped).
        """
        store = self._require_store()
        return await graph_entities_service.list_topics(
            store,
            entity_type=entity_type,
            min_mentions=min_mentions,
            limit=limit,
        )

    async def topic_detail(
        self,
        name: str,
        *,
        max_memories: int = 20,
        include_summary: bool = True,  # accepted for parity; LLM summary is offline in 4B
    ) -> Optional[graph_entities_service.TopicDetail]:
        """Return entity + related entities + recent memories.

        Returns ``None`` when no entity matches ``name``. ``include_summary``
        is accepted for compatibility but the async path doesn't run an
        LLM summarizer in 4B (no enrichment client wired in here).
        """
        store = self._require_store()
        return await graph_entities_service.get_topic_detail(
            store, name, max_memories=max_memories,
        )

    # ── Phase 4B: recent activity ───────────────────────────────────────

    async def recent_activity(
        self,
        *,
        hours: int = 24,
        project: Optional[str] = None,
        limit: int = 100,
    ) -> "RecentActivity":
        """Recent memories grouped by project.

        Mirrors :meth:`Lore.recent_activity` shape (groups + total) but
        returns the lightweight :class:`RecentActivity` defined here —
        the sync ``RecentActivityResult`` carries legacy ``Memory`` rows
        that aren't compatible with ``StoredMemory``.
        """
        hours = max(1, min(hours, 168))
        limit = max(1, min(limit, 200))
        store = self._require_store()
        memories = await recent_service.get_recent_activity(
            store,
            org_id=self.org_id,
            project=project,
            hours=hours,
            max_memories=limit,
        )
        groups: Dict[str, List[StoredMemory]] = {}
        for m in memories:
            groups.setdefault(m.project or "(no project)", []).append(m)
        group_objs = [
            RecentActivityGroup(
                project=proj,
                memories=tuple(rows),
                count=len(rows),
            )
            for proj, rows in groups.items()
        ]
        # Mirror sync: largest groups first
        group_objs.sort(key=lambda g: g.count, reverse=True)
        return RecentActivity(
            groups=tuple(group_objs),
            total_count=sum(g.count for g in group_objs),
            hours=hours,
            generated_at=datetime.now(timezone.utc),
        )

    # ── Phase 4B: review workflow ───────────────────────────────────────

    async def get_pending_reviews(
        self, limit: int = 50
    ) -> Sequence[graph_review_service.PendingReview]:
        """List pending relationships with risk-score, highest-risk first."""
        store = self._require_store()
        listing = await graph_review_service.list_pending_reviews(
            store, limit=limit,
        )
        return listing.pending

    async def review_connection(
        self,
        rel_id: str,
        action: str,
        reason: Optional[str] = None,
    ) -> graph_review_service.ReviewActionResult:
        """Approve or reject a single pending relationship."""
        store = self._require_store()
        return await graph_review_service.review_relationship(
            store, rel_id, action=action, reason=reason,
        )

    async def review_all(
        self,
        action: str,
        reason: Optional[str] = None,
    ) -> int:
        """Apply ``action`` to every currently-pending relationship.

        Returns the number of relationships successfully updated.
        """
        store = self._require_store()
        listing = await graph_review_service.list_pending_reviews(
            store, limit=10000,
        )
        ids = [p.id for p in listing.pending]
        result = await graph_review_service.bulk_review(
            store, ids, action=action, reason=reason,
        )
        return result.updated

    # ── Phase 4B: conversations ─────────────────────────────────────────

    async def add_conversation(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        project: Optional[str] = None,
    ) -> StoredConversationJob:
        """Queue a conversation-extraction job. Returns the queued job row.

        When ``auto_workers=True`` (default), the freshly-created job id
        is enqueued onto the :class:`IngestWorker` queue so the embedded
        background loop will drain it. With ``auto_workers=False`` the
        job is created in ``queued`` state and the caller is responsible
        for invoking ``services.conversations.process_job_async`` (or
        equivalent) to drain it.
        """
        store = self._require_store()
        job = await conversations_create_job(
            store,
            org_id=self.org_id,
            messages=[dict(m) for m in messages],
            user_id=user_id,
            session_id=session_id,
            project=project,
        )
        if self._ingest_queue is not None:
            await self._ingest_queue.put((job.id, self.org_id))
        return job

    async def conversation_status(self, job_id: str) -> StoredConversationJob:
        """Fetch a queued conversation job by id."""
        store = self._require_store()
        return await conversations_get_job_status(store, job_id, self.org_id)

    # ── Phase 4B: stats / on-this-day ───────────────────────────────────

    async def stats(
        self, project: Optional[str] = None
    ) -> "MemoryStats":
        """Aggregate memory statistics.

        The async persistence layer doesn't expose tier/consolidation
        bookkeeping yet, so the returned ``MemoryStats`` is a strict
        subset of what the sync class returns: total + meta-derived
        ``by_type`` + oldest/newest timestamps.
        """
        store = self._require_store()
        rows = list(await memories_service.list_memories(
            store,
            org_id=self.org_id,
            project=project,
            limit=100000,
            include_expired=False,
        ))
        if not rows:
            return MemoryStats(total=0)
        by_type: Counter[str] = Counter()
        for m in rows:
            by_type[(m.meta or {}).get("type", "general")] += 1
        # list_memories returns newest-first.
        return MemoryStats(
            total=len(rows),
            by_type=dict(by_type),
            oldest=rows[-1].created_at,
            newest=rows[0].created_at,
        )

    async def on_this_day(
        self,
        *,
        today: Optional[datetime] = None,
        limit: int = 20,
    ) -> List[StoredMemory]:
        """Memories created on this calendar day (any year), newest first.

        The async persistence layer doesn't have a temporal-query helper,
        so this filters in-process. Cheap for solo-scale corpora; Phase
        4C may push it into the Store.
        """
        anchor = (today or datetime.now(timezone.utc))
        target_month, target_day = anchor.month, anchor.day
        store = self._require_store()
        all_rows = await memories_service.list_memories(
            store,
            org_id=self.org_id,
            limit=100000,
            include_expired=False,
        )
        matches = [
            m for m in all_rows
            if m.created_at.month == target_month
            and m.created_at.day == target_day
        ]
        # Already created_at DESC; trim to limit.
        return list(matches)[:limit]

    # ── Phase 4B: voting ────────────────────────────────────────────────

    async def upvote(self, memory_id: str) -> StoredMemory:
        """Increment a memory's upvote counter."""
        store = self._require_store()
        return await memories_service.vote_memory(
            store, org_id=self.org_id, memory_id=memory_id, direction="up",
        )

    async def downvote(self, memory_id: str) -> StoredMemory:
        """Increment a memory's downvote counter."""
        store = self._require_store()
        return await memories_service.vote_memory(
            store, org_id=self.org_id, memory_id=memory_id, direction="down",
        )

    # ── Phase 4B: consolidation / enrichment / maintenance ──────────────

    async def consolidate(
        self,
        *,
        project: Optional[str] = None,
        dry_run: bool = True,
    ) -> "ConsolidationReport":
        """Embedded consolidation is a no-op until Phase 4C.

        Returns a report with zero counts so callers can use the same
        ``await lore.consolidate(...)`` shape as the sync class. Phase 4C
        will plumb in :class:`lore.consolidation.ConsolidationEngine`
        once that engine is ported to the new ``Store`` protocol.
        """
        # Touch the store to ensure we're inside an open context.
        self._require_store()
        return ConsolidationReport(
            project=project,
            dry_run=dry_run,
            groups_found=0,
            memories_consolidated=0,
            note="consolidate() is a no-op in Phase 4B; wired in 4C.",
        )

    async def get_consolidation_log(
        self,
        *,
        project: Optional[str] = None,
        limit: int = 50,
    ) -> Sequence[Any]:
        """Return an empty log — the async Store doesn't track consolidations yet."""
        self._require_store()
        return ()

    async def enrich_memories(
        self,
        *,
        project: Optional[str] = None,
        limit: int = 10,
    ) -> "EnrichmentReport":
        """Run the LLM enrichment pipeline over recent un-enriched memories.

        Walks at most ``limit`` rows in ``project``, skipping any whose
        ``meta.enrichment`` is already populated, and merges the LLM
        result into the memory's meta JSONB column. Errors are tolerated
        per-memory — the report's ``failed`` counter and ``errors`` list
        capture them.
        """
        store = self._require_store()
        rows = await memories_service.list_memories(
            store,
            org_id=self.org_id,
            project=project,
            limit=limit,
            include_expired=False,
        )
        enriched = skipped = failed = 0
        errors: List[str] = []
        for m in rows:
            if (m.meta or {}).get("enrichment"):
                skipped += 1
                continue
            try:
                await memories_service.enrich_memory_async(
                    store,
                    memory_id=m.id,
                    content=m.content,
                    context=m.context,
                )
                enriched += 1
            except Exception as e:  # pragma: no cover - defensive
                failed += 1
                errors.append(f"{m.id}: {e}")
        return EnrichmentReport(
            enriched=enriched,
            skipped=skipped,
            failed=failed,
            errors=tuple(errors),
        )

    async def cleanup_expired(
        self, importance_threshold: Optional[float] = None  # noqa: ARG002 - parity
    ) -> int:
        """Purge expired memories (TTL-based). Returns rowcount.

        ``importance_threshold`` is accepted for parity with the sync
        ``Lore`` API but is currently ignored — the async layer doesn't
        track importance_score outside of recall scoring. Phase 4C will
        re-introduce importance-based pruning if it proves needed.
        """
        store = self._require_store()
        return await store.expire_memories()

    async def recalculate_importance(
        self, project: Optional[str] = None  # noqa: ARG002 - parity
    ) -> int:
        """Recompute importance for every memory in scope.

        The async Store recomputes importance on every read/recall/vote
        already, so this is currently a no-op that returns 0. Kept for
        symmetry with the sync class so callers can switch implementations
        without code changes.
        """
        self._require_store()
        return 0

    # ── Phase 4B: classify + as_prompt ──────────────────────────────────

    async def classify(self, text: str) -> "Classification":
        """Classify ``text`` using the rule-based classifier.

        The embedded API uses the rule-based classifier by default —
        callers needing the LLM-backed one should classify on the
        sync ``Lore`` instance or through the HTTP API. Wrapping in
        an ``async def`` keeps the embedded surface awaitable so a
        Phase-4C swap-in (LLM classifier with an async client) can
        happen without breaking the call signature.
        """
        # Defer the import: classify modules pull in taxonomy data.
        from lore.classify.rules import RuleBasedClassifier

        self._require_store()
        return RuleBasedClassifier().classify(text)

    async def as_prompt(
        self,
        query: str,
        *,
        format: str = "xml",
        max_tokens: Optional[int] = None,
        max_chars: Optional[int] = None,
        limit: int = 10,
        min_score: float = 0.0,
        include_metadata: bool = False,
        project: Optional[str] = None,
        verbatim: bool = False,
    ) -> str:
        """Recall + format the result as a prompt-ready string.

        Mirrors :meth:`Lore.as_prompt`: vector-recall ``query`` and pass
        the hits through :class:`PromptFormatter`. Returns ``""`` if no
        memories match.
        """
        from lore.prompt.formatter import PromptFormatter
        from lore.types import Memory, RecallResult

        # Use ``min_score`` of 0 inside recall so the formatter sees the
        # full pool; the formatter handles its own filtering.
        hits = await self.recall(query, k=limit, project=project, min_score=0.0)
        if not hits:
            return ""

        # Adapt StoredMemory -> legacy Memory so the formatter (which
        # was written against ``lore.types.Memory``) keeps working.
        results: List[RecallResult] = []
        for h in hits:
            mem = Memory(
                id=h.id,
                content=h.content,
                type=(h.meta or {}).get("type", "general"),
                tier=(h.meta or {}).get("tier", "long"),
                context=h.context,
                tags=list(h.tags or []),
                metadata=dict(h.meta or {}),
                source=h.source,
                project=h.project,
                embedding=None,
                created_at=h.created_at.isoformat() if h.created_at else "",
                updated_at=h.updated_at.isoformat() if h.updated_at else "",
                ttl=None,
                expires_at=h.expires_at.isoformat() if h.expires_at else None,
                confidence=h.confidence,
            )
            score = getattr(h, "score", 0.0)
            results.append(RecallResult(memory=mem, score=score, verbatim=verbatim))

        formatter = PromptFormatter()
        return formatter.format(
            query,
            results,
            format=format,
            max_tokens=max_tokens,
            max_chars=max_chars,
            min_score=min_score,
            include_metadata=include_metadata,
            verbatim=verbatim,
        )


# ── Phase 4B return-type dataclasses ────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RecentActivityGroup:
    """A bucket of memories within a single project, oldest field set first."""

    project: str
    memories: Sequence[StoredMemory]
    count: int


@dataclass(frozen=True, slots=True)
class RecentActivity:
    """Result of :meth:`AsyncLore.recent_activity`."""

    groups: Sequence[RecentActivityGroup]
    total_count: int
    hours: int
    generated_at: datetime


@dataclass(frozen=True, slots=True)
class MemoryStats:
    """Result of :meth:`AsyncLore.stats`. Subset of the sync ``MemoryStats``."""

    total: int
    by_type: Mapping[str, int] = field(default_factory=dict)
    oldest: Optional[datetime] = None
    newest: Optional[datetime] = None


@dataclass(frozen=True, slots=True)
class EnrichmentReport:
    """Result of :meth:`AsyncLore.enrich_memories`."""

    enriched: int
    skipped: int
    failed: int
    errors: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class ConsolidationReport:
    """Placeholder result for :meth:`AsyncLore.consolidate` (no-op until 4C)."""

    project: Optional[str]
    dry_run: bool
    groups_found: int
    memories_consolidated: int
    note: str = ""
