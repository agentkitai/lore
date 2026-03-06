# Architecture: F7 — Webhook / Multi-Source Ingestion

**Version:** 1.0
**Author:** Architect Agent
**Date:** 2026-03-06
**PRD:** `_bmad-output/planning-artifacts/f07-webhook-ingestion-prd.md`
**Depends on:** F6 (Metadata Enrichment), F9 (Dialog Classification), F2 (Fact Extraction) — soft dependencies for auto-enrichment pipeline
**Dependents:** None

---

## 1. Overview

This document specifies how to implement multi-source webhook ingestion for Lore. The feature adds REST endpoints (`POST /ingest`, `POST /ingest/batch`, and adapter-specific webhook routes), pluggable source adapters (Slack, Telegram, Git, raw text), content normalization, deduplication, and auto-enrichment integration. It also adds an `ingest` MCP tool and CLI subcommand.

### Architecture Principles

1. **Ingestion is a front door to `remember()`** — The ingestion layer handles receiving, verifying, normalizing, deduplicating, and source-tracking. Actual storage and enrichment delegates to `lore.remember()`.
2. **Adapter isolation** — Each source adapter is a standalone module responsible for payload parsing, webhook verification, and format-specific normalization. Adding a new adapter requires zero changes to the pipeline.
3. **Fail-safe enrichment** — Auto-enrichment on ingest is optional and never blocks storage. If any LLM step fails, the memory is saved without enrichment (consistent with existing `remember()` behavior).
4. **Security first** — Webhook signature verification uses constant-time comparison. API keys are scoped to `ingest` only. Secrets are never logged.
5. **No new required dependencies** — Uses stdlib (`hmac`, `hashlib`, `asyncio`, `re`) and existing FastAPI/SQLite/embedding infrastructure.
6. **Dedup before enrichment** — Duplicates are detected before expensive LLM calls. Exact source ID match is O(1); content similarity uses the existing embedding + search infrastructure.

---

## 2. Module Structure

```
src/lore/ingest/
    __init__.py              # Public API: IngestPipeline, IngestResult, NormalizedMessage
    pipeline.py              # IngestionPipeline orchestrator
    normalize.py             # Content normalization (shared across adapters)
    dedup.py                 # Deduplication (exact ID + content similarity)
    queue.py                 # IngestionQueue (asyncio, in-process)
    auth.py                  # Ingest-specific API key validation
    rate_limit.py            # Per-key + per-adapter rate limiting wrapper
    adapters/
        __init__.py          # Adapter registry: get_adapter(), ADAPTERS dict
        base.py              # SourceAdapter ABC, NormalizedMessage dataclass
        slack.py             # SlackAdapter (mrkdwn stripping, HMAC-SHA256 verification)
        telegram.py          # TelegramAdapter (HTML/Markdown stripping, token verification)
        git.py               # GitAdapter (commit message normalization, GitHub/GitLab webhook secret)
        raw.py               # RawAdapter (passthrough, no verification)
```

Server-side additions:
```
src/lore/server/routes/
    ingest.py                # /ingest, /ingest/batch, /ingest/webhook/* endpoints
```

### 2.1 Dependency Graph

```
ingest/__init__.py
  ├── exports: IngestPipeline, IngestResult, NormalizedMessage
  └── imports from: pipeline.py, adapters/base.py

ingest/adapters/base.py
  ├── SourceAdapter (ABC)
  ├── NormalizedMessage (dataclass)
  └── imports: dataclasses, typing (stdlib only)

ingest/adapters/{slack,telegram,git,raw}.py
  ├── Each extends SourceAdapter
  └── imports: base.py, hmac/hashlib (stdlib), normalize.py

ingest/normalize.py
  ├── normalize_content(text, format) → str
  ├── _strip_slack_mrkdwn(text) → str
  ├── _strip_telegram_formatting(text) → str
  ├── _normalize_git_message(text) → str
  ├── _collapse_whitespace(text) → str
  ├── _strip_invisible_chars(text) → str
  └── imports: re (stdlib only)

ingest/dedup.py
  ├── Deduplicator
  ├── DedupResult (dataclass)
  └── imports: store/base.py (Store), embed/base.py (Embedder)

ingest/pipeline.py
  ├── IngestionPipeline
  ├── IngestResult (dataclass)
  └── imports: adapters/, normalize.py, dedup.py, lore.py (Lore.remember)

ingest/queue.py
  ├── IngestionQueue
  └── imports: asyncio, pipeline.py

ingest/auth.py
  ├── validate_ingest_key(key, source) → IngestAuthContext
  └── imports: server/auth.py (existing auth infra)

ingest/rate_limit.py
  ├── IngestRateLimiter (wraps existing RateLimitBackend)
  └── imports: server/rate_limit.py

server/routes/ingest.py
  ├── POST /ingest
  ├── POST /ingest/batch
  ├── POST /ingest/webhook/{adapter}
  ├── GET /ingest/status/{tracking_id}
  └── imports: ingest/, server/auth.py, server/rate_limit.py

mcp/server.py
  └── ingest() tool added

cli.py
  └── ingest subcommand added
```

---

## 3. Data Models

### 3.1 `NormalizedMessage` — Common Adapter Output

```python
# src/lore/ingest/adapters/base.py

from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class NormalizedMessage:
    """Common format produced by all source adapters after parsing + normalization."""

    content: str                                # Plain text, formatting stripped
    user: Optional[str] = None                  # Original author identity
    channel: Optional[str] = None               # Source channel/repo/chat
    timestamp: Optional[str] = None             # ISO 8601 from source system
    source_message_id: Optional[str] = None     # Platform-specific unique ID (for dedup)
    raw_format: str = "plain_text"              # Original format before normalization
    memory_type: str = "general"                # Adapter-suggested memory type
    tags: Optional[List[str]] = None            # Adapter-suggested tags
```

### 3.2 `IngestResult` — Pipeline Output

```python
# src/lore/ingest/pipeline.py

from dataclasses import dataclass
from typing import Optional


@dataclass
class IngestResult:
    """Result of a single ingestion attempt."""

    status: str                                  # "ingested" | "duplicate_rejected" | "duplicate_skipped"
                                                 # | "duplicate_merged" | "failed" | "queued"
    memory_id: Optional[str] = None              # Set if memory was created
    duplicate_of: Optional[str] = None           # Set if duplicate detected
    similarity: float = 0.0                      # Cosine similarity (if content dedup)
    dedup_strategy: str = ""                     # "exact_id" | "content_similarity" | ""
    enriched: bool = False                       # Whether enrichment ran
    tracking_id: Optional[str] = None            # Set if queued (async mode)
    error: Optional[str] = None                  # Set if failed
```

### 3.3 `DedupResult` — Deduplication Check Output

```python
# src/lore/ingest/dedup.py

from dataclasses import dataclass
from typing import Optional


@dataclass
class DedupResult:
    """Result of deduplication check."""

    is_duplicate: bool
    duplicate_of: Optional[str] = None           # Memory ID of existing duplicate
    similarity: float = 0.0
    strategy: str = ""                           # "exact_id" | "content_similarity"
```

### 3.4 Source Metadata — Stored in `memory.metadata["source_info"]`

No new fields on the `Memory` dataclass. Source tracking is stored in the existing `metadata` JSONB field:

```python
memory.metadata["source_info"] = {
    "adapter": "slack",                          # slack | telegram | git | raw | mcp
    "channel": "#engineering",                   # Source-specific location
    "user": "alice@company.com",                 # Original author
    "original_timestamp": "2026-03-06T14:30:00Z",
    "ingested_at": "2026-03-06T14:30:05Z",
    "source_message_id": "1709734200.123456",    # Platform-specific ID
    "raw_format": "slack_mrkdwn",                # Format before normalization
}
```

The `memory.source` field (already on `Memory`) is set to the adapter name (e.g., `"slack"`, `"telegram"`, `"git"`, `"raw"`).

---

## 4. Source Adapter Pattern

### 4.1 Abstract Base — `SourceAdapter`

```python
# src/lore/ingest/adapters/base.py

from abc import ABC, abstractmethod
from typing import Optional


class SourceAdapter(ABC):
    """Base class for source adapters.

    Each adapter handles:
    1. Webhook verification — validate request authenticity
    2. Payload normalization — parse source format into NormalizedMessage
    """

    adapter_name: str = "raw"

    def verify(self, request_headers: dict, request_body: bytes) -> bool:
        """Verify webhook signature. Returns True if valid or not applicable.

        Implementations MUST use hmac.compare_digest() for constant-time comparison.
        """
        return True

    @abstractmethod
    def normalize(self, payload: dict) -> NormalizedMessage:
        """Parse source-specific payload and return normalized message."""
        ...
```

### 4.2 Adapter Registry

```python
# src/lore/ingest/adapters/__init__.py

from .slack import SlackAdapter
from .telegram import TelegramAdapter
from .git import GitAdapter
from .raw import RawAdapter

# Registry: adapter_name → class
ADAPTERS = {
    "slack": SlackAdapter,
    "telegram": TelegramAdapter,
    "git": GitAdapter,
    "raw": RawAdapter,
}


def get_adapter(name: str, **kwargs) -> "SourceAdapter":
    """Look up adapter by name. Raises ValueError for unknown adapters."""
    cls = ADAPTERS.get(name)
    if cls is None:
        raise ValueError(f"Unknown source adapter: {name}")
    return cls(**kwargs)
```

Adapter construction requires source-specific secrets (e.g., `SlackAdapter(signing_secret=...)`) which are resolved from environment variables at server startup (see §10 Configuration).

### 4.3 SlackAdapter

```python
# src/lore/ingest/adapters/slack.py

import hashlib
import hmac
import re
import time
from typing import Optional

from .base import NormalizedMessage, SourceAdapter
from ..normalize import normalize_content


class SlackAdapter(SourceAdapter):
    adapter_name = "slack"

    def __init__(self, signing_secret: str):
        self.signing_secret = signing_secret

    def verify(self, request_headers: dict, request_body: bytes) -> bool:
        """Verify Slack request using HMAC-SHA256 of v0:{timestamp}:{body}.

        Also enforces replay protection: rejects timestamps older than 5 minutes.
        """
        timestamp = request_headers.get("x-slack-request-timestamp", "")
        signature = request_headers.get("x-slack-signature", "")

        # Replay protection
        try:
            if abs(time.time() - float(timestamp)) > 300:
                return False
        except (ValueError, TypeError):
            return False

        # Compute expected signature
        sig_basestring = f"v0:{timestamp}:{request_body.decode('utf-8')}"
        expected = "v0=" + hmac.new(
            self.signing_secret.encode(),
            sig_basestring.encode(),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, signature)

    def normalize(self, payload: dict) -> NormalizedMessage:
        event = payload.get("event", {})

        return NormalizedMessage(
            content=normalize_content(event.get("text", ""), "slack_mrkdwn"),
            user=event.get("user"),
            channel=event.get("channel"),
            timestamp=event.get("ts"),
            source_message_id=event.get("ts"),
            raw_format="slack_mrkdwn",
        )

    @staticmethod
    def is_url_verification(payload: dict) -> bool:
        """Check if this is a Slack URL verification challenge."""
        return payload.get("type") == "url_verification"

    @staticmethod
    def is_bot_message(payload: dict) -> bool:
        """Check if this is a bot message (ignore to avoid feedback loops)."""
        event = payload.get("event", {})
        return event.get("subtype") == "bot_message" or event.get("bot_id") is not None
```

### 4.4 TelegramAdapter

```python
# src/lore/ingest/adapters/telegram.py

import hashlib
from datetime import datetime, timezone
from typing import Optional

from .base import NormalizedMessage, SourceAdapter
from ..normalize import normalize_content


class TelegramAdapter(SourceAdapter):
    adapter_name = "telegram"

    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.secret_token = hashlib.sha256(bot_token.encode()).hexdigest()[:32]

    def verify(self, request_headers: dict, request_body: bytes) -> bool:
        """Verify Telegram X-Telegram-Bot-Api-Secret-Token header."""
        import hmac as _hmac

        header_token = request_headers.get("x-telegram-bot-api-secret-token", "")
        return _hmac.compare_digest(header_token, self.secret_token)

    def normalize(self, payload: dict) -> NormalizedMessage:
        message = payload.get("message", {})
        chat = message.get("chat", {})
        user = message.get("from", {})

        raw_text = message.get("text", "")
        has_entities = bool(message.get("entities"))
        raw_format = "telegram_html" if has_entities else "plain_text"

        return NormalizedMessage(
            content=normalize_content(raw_text, raw_format),
            user=user.get("username") or str(user.get("id", "")),
            channel=chat.get("title") or str(chat.get("id", "")),
            timestamp=datetime.fromtimestamp(
                message.get("date", 0), tz=timezone.utc
            ).isoformat(),
            source_message_id=str(message.get("message_id", "")),
            raw_format=raw_format,
        )
```

### 4.5 GitAdapter

```python
# src/lore/ingest/adapters/git.py

import hashlib
import hmac
from typing import Optional

from .base import NormalizedMessage, SourceAdapter
from ..normalize import normalize_content


class GitAdapter(SourceAdapter):
    adapter_name = "git"

    def __init__(self, webhook_secret: Optional[str] = None):
        self.webhook_secret = webhook_secret

    def verify(self, request_headers: dict, request_body: bytes) -> bool:
        """Verify GitHub/GitLab webhook via X-Hub-Signature-256 header."""
        if not self.webhook_secret:
            return True  # No secret configured → skip verification

        signature = request_headers.get("x-hub-signature-256", "")
        if not signature.startswith("sha256="):
            return False

        expected = "sha256=" + hmac.new(
            self.webhook_secret.encode(),
            request_body,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, signature)

    def normalize(self, payload: dict) -> NormalizedMessage:
        # Handle both GitHub webhook format and simple {message, author, sha} format
        commits = payload.get("commits", [payload])
        messages = []
        for commit in commits:
            msg = commit.get("message", "")
            if msg:
                messages.append(msg)

        first_commit = commits[0] if commits else {}
        author = first_commit.get("author", {})
        user = author.get("email") if isinstance(author, dict) else str(author)

        repo = payload.get("repository", {})
        repo_name = repo.get("full_name") if isinstance(repo, dict) else payload.get("repo", "")

        return NormalizedMessage(
            content=normalize_content("\n\n".join(messages), "git_commit"),
            user=user,
            channel=str(repo_name),
            timestamp=first_commit.get("timestamp") or first_commit.get("date", ""),
            source_message_id=first_commit.get("id") or first_commit.get("sha", ""),
            raw_format="git_commit",
            memory_type="code",
            tags=["git-commit"],
        )
```

### 4.6 RawAdapter

```python
# src/lore/ingest/adapters/raw.py

from .base import NormalizedMessage, SourceAdapter
from ..normalize import normalize_content


class RawAdapter(SourceAdapter):
    adapter_name = "raw"

    def normalize(self, payload: dict) -> NormalizedMessage:
        return NormalizedMessage(
            content=normalize_content(payload.get("content", ""), "plain_text"),
            user=payload.get("user"),
            channel=payload.get("channel"),
            timestamp=payload.get("timestamp"),
            source_message_id=payload.get("message_id"),
            raw_format="plain_text",
            memory_type=payload.get("type", "general"),
            tags=payload.get("tags"),
        )
```

---

## 5. Content Normalization

### 5.1 `src/lore/ingest/normalize.py`

```python
"""Content normalization — strip formatting, clean whitespace, enforce limits."""

import re
from typing import Optional

MAX_CONTENT_LENGTH = 10_000


def normalize_content(text: str, format: str = "plain_text") -> str:
    """Normalize content from various source formats to clean plain text.

    Pipeline:
    1. Strip source-specific formatting (mrkdwn, HTML, etc.)
    2. Collapse excessive whitespace
    3. Remove zero-width and invisible Unicode characters
    4. Trim to max content length
    5. Strip leading/trailing whitespace
    """
    if not text:
        return ""

    if format == "slack_mrkdwn":
        text = _strip_slack_mrkdwn(text)
    elif format in ("telegram_html", "telegram_markdown"):
        text = _strip_telegram_formatting(text)
    elif format == "git_commit":
        text = _normalize_git_message(text)

    text = _collapse_whitespace(text)
    text = _strip_invisible_chars(text)
    text = text[:MAX_CONTENT_LENGTH].strip()
    return text


def _strip_slack_mrkdwn(text: str) -> str:
    """Convert Slack mrkdwn to plain text."""
    # User mentions: <@U123ABC> → @user
    text = re.sub(r"<@([A-Z0-9]+)>", r"@\1", text)
    # Channel references: <#C123|channel-name> → #channel-name
    text = re.sub(r"<#[A-Z0-9]+\|([^>]+)>", r"#\1", text)
    # URLs: <http://example.com|label> → label, <http://example.com> → http://example.com
    text = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2", text)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    # Bold, italic, strikethrough
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    text = re.sub(r"~([^~]+)~", r"\1", text)
    # Code blocks: ```code``` → code
    text = re.sub(r"```[^\n]*\n?(.*?)```", r"\1", text, flags=re.DOTALL)
    # Inline code: `code` → code
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text


def _strip_telegram_formatting(text: str) -> str:
    """Strip Telegram HTML/Markdown entities to plain text."""
    # HTML tags: <b>, <i>, <code>, <pre>, <a href="...">
    text = re.sub(r"<a\s+href=\"[^\"]*\">([^<]*)</a>", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    # Markdown bold/italic
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    # Code blocks
    text = re.sub(r"```[^\n]*\n?(.*?)```", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text


def _normalize_git_message(text: str) -> str:
    """Normalize git commit message — keep subject + body, strip diff hunks."""
    # Remove diff-stat lines (e.g., " 3 files changed, 10 insertions(+)")
    text = re.sub(r"^\s*\d+ files? changed.*$", "", text, flags=re.MULTILINE)
    # Remove diff hunks (@@...@@)
    text = re.sub(r"^@@.*@@.*$", "", text, flags=re.MULTILINE)
    # Remove diff +/- lines
    text = re.sub(r"^[+-]{3}\s.*$", "", text, flags=re.MULTILINE)
    # Keep Signed-off-by, Co-authored-by as-is (useful metadata)
    return text


def _collapse_whitespace(text: str) -> str:
    """Collapse runs of whitespace (preserving single newlines)."""
    # Collapse multiple blank lines into one
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse horizontal whitespace
    text = re.sub(r"[^\S\n]+", " ", text)
    return text


def _strip_invisible_chars(text: str) -> str:
    """Remove zero-width characters and other invisible Unicode."""
    # Zero-width space, zero-width joiner, zero-width non-joiner, BOM, etc.
    text = re.sub(r"[\u200b\u200c\u200d\ufeff\u00ad\u2060\u180e]", "", text)
    return text
```

---

## 6. Webhook Verification

### 6.1 Verification Strategy per Platform

| Platform | Method | Header(s) | Secret Source | Replay Protection |
|----------|--------|-----------|---------------|-------------------|
| **Slack** | HMAC-SHA256 of `v0:{timestamp}:{body}` | `X-Slack-Request-Timestamp`, `X-Slack-Signature` | `LORE_SLACK_SIGNING_SECRET` env var | Reject if timestamp > 5 min old |
| **Telegram** | Token-derived secret in header | `X-Telegram-Bot-Api-Secret-Token` | SHA-256 of `LORE_TELEGRAM_BOT_TOKEN` (first 32 chars) | None (Telegram doesn't retry) |
| **Git (GitHub)** | HMAC-SHA256 of body | `X-Hub-Signature-256` | `LORE_GIT_WEBHOOK_SECRET` env var | None (GitHub uses unique delivery IDs) |
| **Raw** | None (API key only) | — | — | — |

### 6.2 Security Requirements

1. **Constant-time comparison** — All signature checks use `hmac.compare_digest()` to prevent timing attacks.
2. **Verification before processing** — Signature check runs before payload parsing or any business logic.
3. **Secrets never logged** — Adapter constructors accept secrets as arguments; logging must not include them.
4. **Dual verification** — Webhook endpoints require BOTH platform-specific verification AND API key authentication. The webhook signature proves the payload came from the platform; the API key proves the endpoint is authorized for ingestion.

### 6.3 Verification Flow (in route handler)

```python
async def handle_webhook(adapter_name: str, request: Request):
    body = await request.body()
    headers = dict(request.headers)

    # Step 1: Platform signature verification
    adapter = get_adapter(adapter_name, **adapter_secrets)
    if not adapter.verify(headers, body):
        raise HTTPException(401, "Webhook signature verification failed")

    # Step 2: API key authentication (query param for webhooks)
    api_key = request.query_params.get("key") or headers.get("authorization", "").removeprefix("Bearer ")
    auth_ctx = validate_ingest_key(api_key, adapter_name)

    # Step 3: Parse + process
    payload = json.loads(body)
    ...
```

---

## 7. Deduplication

### 7.1 `src/lore/ingest/dedup.py`

```python
"""Near-duplicate detection for ingested content."""

from dataclasses import dataclass
from typing import Optional, List

from ..store.base import Store
from ..types import Memory


@dataclass
class DedupResult:
    is_duplicate: bool
    duplicate_of: Optional[str] = None
    similarity: float = 0.0
    strategy: str = ""  # "exact_id" | "content_similarity"


class Deduplicator:
    """Two-strategy deduplication: exact source ID match + content similarity."""

    def __init__(
        self,
        store: Store,
        embedder,  # Embedder instance (from embed/)
        threshold: float = 0.95,
    ):
        self.store = store
        self.embedder = embedder
        self.threshold = threshold

    def check(
        self,
        normalized: "NormalizedMessage",
        adapter_name: str,
        project: Optional[str] = None,
    ) -> DedupResult:
        """Check if content is a near-duplicate of an existing memory.

        Strategy 1: Exact source message ID match (O(1) lookup).
        Strategy 2: Content embedding similarity (top-5, 24h window).
        """
        # Strategy 1: Exact source message ID
        if normalized.source_message_id:
            existing = self._find_by_source_id(
                normalized.source_message_id, adapter_name, project
            )
            if existing:
                return DedupResult(
                    is_duplicate=True,
                    duplicate_of=existing.id,
                    similarity=1.0,
                    strategy="exact_id",
                )

        # Strategy 2: Content similarity
        if not normalized.content.strip():
            return DedupResult(is_duplicate=False)

        embedding = self.embedder.embed(normalized.content)
        similar = self.store.search(
            embedding=embedding,
            project=project,
            limit=5,
            min_confidence=0.0,
        )
        for result in similar:
            if result.score >= self.threshold:
                return DedupResult(
                    is_duplicate=True,
                    duplicate_of=result.memory.id,
                    similarity=result.score,
                    strategy="content_similarity",
                )

        return DedupResult(is_duplicate=False)

    def _find_by_source_id(
        self, source_message_id: str, adapter_name: str, project: Optional[str]
    ) -> Optional[Memory]:
        """Search for existing memory with matching source_info.source_message_id.

        Uses Store.list() with metadata filtering. For SQLite, this scans
        metadata JSON; for Postgres, this uses JSONB indexing.
        """
        candidates = self.store.list(project=project, limit=100)
        for mem in candidates:
            si = (mem.metadata or {}).get("source_info", {})
            if (
                si.get("source_message_id") == source_message_id
                and si.get("adapter") == adapter_name
            ):
                return mem
        return None
```

### 7.2 Dedup Modes

| Mode | Behavior | HTTP Status |
|------|----------|-------------|
| `reject` (default) | Return error with duplicate memory ID | 409 Conflict |
| `merge` | Append new source_info to existing memory's metadata (multi-source attribution) | 200 OK |
| `skip` | Silently accept but don't store | 200 OK with `"status": "duplicate_skipped"` |
| `allow` | Disable dedup entirely; store even if duplicate | 201 Created |

### 7.3 Future Optimization: Source ID Index

For V1, exact source ID dedup scans recent memories via `store.list()`. This is acceptable for moderate ingestion volume. In a future version, a dedicated index on `metadata->'source_info'->>'source_message_id'` (Postgres) or a source_id lookup table (SQLite) would make this O(1). This is not needed for V1 given the 100-item scan limit.

---

## 8. Ingestion Pipeline

### 8.1 `src/lore/ingest/pipeline.py`

```python
"""Ingestion pipeline — orchestrates normalize → dedup → remember."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..lore import Lore
from .adapters.base import NormalizedMessage, SourceAdapter
from .dedup import DedupResult, Deduplicator
from .normalize import normalize_content

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    status: str
    memory_id: Optional[str] = None
    duplicate_of: Optional[str] = None
    similarity: float = 0.0
    dedup_strategy: str = ""
    enriched: bool = False
    tracking_id: Optional[str] = None
    error: Optional[str] = None


class IngestionPipeline:
    """Full ingestion pipeline from raw payload to stored memory.

    Pipeline stages:
    1. Adapter selection + normalization
    2. Content validation
    3. Deduplication check
    4. Delegate to Lore.remember() (which handles embedding, enrichment, facts, graph)
    """

    def __init__(
        self,
        lore: Lore,
        deduplicator: Deduplicator,
        default_dedup_mode: str = "reject",
        auto_enrich: bool = True,
    ):
        self.lore = lore
        self.deduplicator = deduplicator
        self.default_dedup_mode = default_dedup_mode
        self.auto_enrich = auto_enrich

    def ingest(
        self,
        adapter: SourceAdapter,
        payload: dict,
        *,
        project: Optional[str] = None,
        dedup_mode: Optional[str] = None,
        enrich: Optional[bool] = None,
        extra_tags: Optional[List[str]] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> IngestResult:
        """Run full ingestion pipeline for a single item.

        Returns IngestResult with status and memory_id (if created).
        """
        mode = dedup_mode or self.default_dedup_mode
        should_enrich = enrich if enrich is not None else self.auto_enrich

        # Stage 1: Normalize
        normalized = adapter.normalize(payload)

        # Stage 2: Validate
        if not normalized.content or not normalized.content.strip():
            return IngestResult(status="failed", error="Content is empty after normalization")

        # Stage 3: Dedup
        if mode != "allow":
            dedup = self.deduplicator.check(normalized, adapter.adapter_name, project)
            if dedup.is_duplicate:
                if mode == "reject":
                    return IngestResult(
                        status="duplicate_rejected",
                        duplicate_of=dedup.duplicate_of,
                        similarity=dedup.similarity,
                        dedup_strategy=dedup.strategy,
                    )
                elif mode == "skip":
                    return IngestResult(
                        status="duplicate_skipped",
                        duplicate_of=dedup.duplicate_of,
                        similarity=dedup.similarity,
                        dedup_strategy=dedup.strategy,
                    )
                elif mode == "merge":
                    self._merge_source_info(dedup.duplicate_of, normalized, adapter.adapter_name)
                    return IngestResult(
                        status="duplicate_merged",
                        duplicate_of=dedup.duplicate_of,
                        similarity=dedup.similarity,
                        dedup_strategy=dedup.strategy,
                    )

        # Stage 4: Build source_info metadata
        source_info = self._build_source_info(normalized, adapter.adapter_name)
        metadata = dict(extra_metadata) if extra_metadata else {}
        metadata["source_info"] = source_info

        # Stage 5: Delegate to lore.remember()
        tags = list(normalized.tags or [])
        if extra_tags:
            tags.extend(extra_tags)

        try:
            memory_id = self.lore.remember(
                content=normalized.content,
                type=normalized.memory_type,
                tier="long",  # Ingested content defaults to long-term
                tags=tags,
                metadata=metadata,
                source=adapter.adapter_name,
                project=project,
            )
        except Exception as e:
            logger.error("Ingestion storage failed: %s", e, exc_info=True)
            return IngestResult(status="failed", error=str(e))

        return IngestResult(
            status="ingested",
            memory_id=memory_id,
            enriched=should_enrich and self.lore._enrichment_pipeline is not None,
        )

    def ingest_batch(
        self,
        items: List[dict],
        adapter: SourceAdapter,
        *,
        project: Optional[str] = None,
        dedup_mode: Optional[str] = None,
        enrich: Optional[bool] = None,
    ) -> List[IngestResult]:
        """Ingest a batch of items. Returns per-item results."""
        results = []
        for item in items:
            result = self.ingest(
                adapter=adapter,
                payload=item,
                project=project,
                dedup_mode=dedup_mode,
                enrich=enrich,
            )
            results.append(result)
        return results

    def _build_source_info(self, normalized: NormalizedMessage, adapter_name: str) -> dict:
        return {
            "adapter": adapter_name,
            "channel": normalized.channel,
            "user": normalized.user,
            "original_timestamp": normalized.timestamp,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "source_message_id": normalized.source_message_id,
            "raw_format": normalized.raw_format,
        }

    def _merge_source_info(
        self, memory_id: str, normalized: NormalizedMessage, adapter_name: str
    ) -> None:
        """Append source_info to existing memory for multi-source attribution."""
        memory = self.lore._store.get(memory_id)
        if not memory:
            return
        meta = dict(memory.metadata) if memory.metadata else {}
        existing_sources = meta.get("additional_sources", [])
        existing_sources.append(self._build_source_info(normalized, adapter_name))
        meta["additional_sources"] = existing_sources
        memory.metadata = meta
        self.lore._store.update(memory)
```

### 8.2 Pipeline Flow Diagram

```
Incoming request
  │
  ├─► Authenticate (API key from header or query param)
  │     └─► 401 if missing/invalid, 403 if wrong scope/source
  │
  ├─► Rate limit check (per-key + per-adapter + global)
  │     └─► 429 if exceeded
  │
  ├─► Select adapter (from "source" field or webhook URL path)
  │
  ├─► Verify webhook signature (adapter.verify())
  │     └─► 401 if invalid
  │
  ├─► Normalize content (adapter.normalize() + normalize_content())
  │     └─► 400 if empty after normalization
  │
  ├─► Dedup check (exact source ID → content similarity)
  │     ├─► 409 (reject) / 200 (skip/merge) if duplicate
  │     └─► Continue if unique
  │
  ├─► Build source_info metadata
  │
  ├─► lore.remember() — handles:
  │     ├─► Security scan + redaction
  │     ├─► Embedding
  │     ├─► Classification (F9, if enabled)
  │     ├─► Enrichment (F6, if enabled)
  │     ├─► Store.save()
  │     ├─► Fact extraction (F2, if enabled)
  │     └─► Graph update (F1, if enabled)
  │
  └─► Return IngestResult (201 Created with memory_id)
```

**Key design decision:** The ingestion pipeline does NOT directly call F6/F9/F2. It delegates to `lore.remember()` which already orchestrates the full enrichment pipeline. This avoids duplicating pipeline logic and ensures ingested memories go through the exact same processing as manually remembered ones.

**Exception: Pipeline order differs from `remember()` in one way** — Dedup runs before `remember()` is called, so duplicate content never triggers LLM enrichment calls. This is intentional to avoid wasting LLM budget on duplicates.

---

## 9. Rate Limiting + Queue

### 9.1 Rate Limiting

```python
# src/lore/ingest/rate_limit.py

"""Ingestion-specific rate limiting — wraps existing RateLimitBackend."""

from typing import Optional, Tuple

from ..server.rate_limit import RateLimitBackend, MemoryBackend


class IngestRateLimiter:
    """Three-level rate limiting for ingestion endpoints.

    Level 1: Per API key — default 100 req/min, configurable per key
    Level 2: Per source adapter — default 200 req/min
    Level 3: Global — default 1000 req/min
    """

    def __init__(
        self,
        backend: Optional[RateLimitBackend] = None,
        per_key_limit: int = 100,
        per_adapter_limit: int = 200,
        global_limit: int = 1000,
        window_seconds: int = 60,
    ):
        self.backend = backend or MemoryBackend(
            max_requests=global_limit, window_seconds=window_seconds
        )
        self.per_key_limit = per_key_limit
        self.per_adapter_limit = per_adapter_limit
        self.global_limit = global_limit
        self.window_seconds = window_seconds

        # Separate backends for each scope
        self._key_backends: dict = {}      # key_id → MemoryBackend
        self._adapter_backends: dict = {}  # adapter_name → MemoryBackend

    def check(
        self, key_id: str, adapter_name: str, key_rate_limit: Optional[int] = None
    ) -> Tuple[bool, dict]:
        """Check all three rate limit levels.

        Returns (allowed, headers_dict).
        headers_dict contains X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset.
        """
        key_limit = key_rate_limit or self.per_key_limit

        # Check per-key
        key_backend = self._key_backends.setdefault(
            key_id, MemoryBackend(max_requests=key_limit, window_seconds=self.window_seconds)
        )
        allowed, retry_after, remaining, limit = key_backend.is_allowed(key_id)
        if not allowed:
            return False, self._build_headers(limit, remaining, retry_after)

        # Check per-adapter
        adapter_backend = self._adapter_backends.setdefault(
            adapter_name,
            MemoryBackend(max_requests=self.per_adapter_limit, window_seconds=self.window_seconds),
        )
        allowed, retry_after, remaining, limit = adapter_backend.is_allowed(adapter_name)
        if not allowed:
            return False, self._build_headers(limit, remaining, retry_after)

        # Check global
        allowed, retry_after, remaining, limit = self.backend.is_allowed("global")
        if not allowed:
            return False, self._build_headers(limit, remaining, retry_after)

        return True, self._build_headers(key_limit, remaining, 0)

    def _build_headers(self, limit: int, remaining: int, retry_after: float) -> dict:
        import time

        headers = {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(max(0, remaining)),
            "X-RateLimit-Reset": str(int(time.time()) + int(retry_after)),
        }
        if retry_after > 0:
            headers["Retry-After"] = str(int(retry_after))
        return headers
```

### 9.2 Internal Queue

```python
# src/lore/ingest/queue.py

"""In-process async queue for burst ingestion."""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional
from ulid import ULID

logger = logging.getLogger(__name__)


@dataclass
class QueueItem:
    tracking_id: str
    adapter_name: str
    payload: dict
    project: Optional[str] = None
    dedup_mode: Optional[str] = None
    enrich: Optional[bool] = None
    status: str = "queued"        # queued | processing | done | failed
    result: Optional[dict] = None


class IngestionQueue:
    """Async in-process queue for decoupling request acceptance from processing.

    When enabled, POST /ingest returns 202 Accepted immediately with a tracking_id.
    Background workers process items sequentially.

    This is in-process only (asyncio.Queue). Queued items are lost on server restart.
    """

    def __init__(self, max_size: int = 1000, workers: int = 2):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_size)
        self._workers = workers
        self._items: dict = {}  # tracking_id → QueueItem
        self._tasks: list = []

    async def start(self, pipeline: "IngestionPipeline", adapters: dict):
        """Start worker tasks."""
        for i in range(self._workers):
            task = asyncio.create_task(self._worker(pipeline, adapters, i))
            self._tasks.append(task)

    async def stop(self):
        """Signal workers to stop and wait for them."""
        for _ in self._tasks:
            await self._queue.put(None)  # Sentinel
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def enqueue(self, item: QueueItem) -> str:
        """Add item to queue. Raises asyncio.QueueFull if full."""
        self._items[item.tracking_id] = item
        await self._queue.put(item.tracking_id)
        return item.tracking_id

    def get_status(self, tracking_id: str) -> Optional[QueueItem]:
        return self._items.get(tracking_id)

    async def _worker(self, pipeline, adapters, worker_id: int):
        """Process queued items one at a time."""
        while True:
            tracking_id = await self._queue.get()
            if tracking_id is None:
                break  # Shutdown sentinel

            item = self._items.get(tracking_id)
            if not item:
                self._queue.task_done()
                continue

            item.status = "processing"
            try:
                from .adapters import get_adapter

                adapter = get_adapter(item.adapter_name, **adapters.get(item.adapter_name, {}))
                result = pipeline.ingest(
                    adapter=adapter,
                    payload=item.payload,
                    project=item.project,
                    dedup_mode=item.dedup_mode,
                    enrich=item.enrich,
                )
                item.status = "done"
                item.result = {
                    "status": result.status,
                    "memory_id": result.memory_id,
                    "enriched": result.enriched,
                }
            except Exception as e:
                logger.error("Queue worker %d failed: %s", worker_id, e, exc_info=True)
                item.status = "failed"
                item.result = {"status": "failed", "error": str(e)}
            finally:
                self._queue.task_done()
```

---

## 10. REST API Design

### 10.1 `src/lore/server/routes/ingest.py`

```python
"""Ingestion REST endpoints — /ingest, /ingest/batch, /ingest/webhook/*"""

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional

router = APIRouter(prefix="/ingest", tags=["ingest"])


# ── Request/Response Models ──────────────────────────────────

class IngestRequest(BaseModel):
    source: str = "raw"                          # Adapter name
    payload: Optional[Dict[str, Any]] = None     # Source-specific payload
    content: Optional[str] = None                # Shorthand for raw text
    user: Optional[str] = None                   # For raw shorthand
    channel: Optional[str] = None                # For raw shorthand
    type: Optional[str] = None                   # Memory type override
    tags: Optional[List[str]] = None             # Extra tags
    project: Optional[str] = None                # Project scope
    enrich: Optional[bool] = None                # Override auto-enrich
    dedup_mode: Optional[str] = None             # reject | merge | skip | allow


class IngestResponse(BaseModel):
    status: str
    memory_id: Optional[str] = None
    source: Optional[str] = None
    enriched: bool = False
    dedup_check: str = "unique"                  # "unique" | "duplicate_rejected" | etc.
    duplicate_of: Optional[str] = None
    similarity: float = 0.0
    tracking_id: Optional[str] = None


class BatchIngestRequest(BaseModel):
    items: List[Dict[str, Any]]                  # Array of payloads
    source: str = "raw"                          # Default adapter for all items
    project: Optional[str] = None
    enrich: Optional[bool] = None
    dedup_mode: Optional[str] = None


class BatchItemResult(BaseModel):
    index: int
    status: str
    memory_id: Optional[str] = None
    duplicate_of: Optional[str] = None
    error: Optional[str] = None


class BatchIngestResponse(BaseModel):
    status: str = "batch_complete"
    total: int
    ingested: int
    duplicates_skipped: int = 0
    failed: int = 0
    results: List[BatchItemResult]


# ── Endpoints ────────────────────────────────────────────────

@router.post("", status_code=201)
async def ingest_single(req: IngestRequest, request: Request) -> IngestResponse:
    """Ingest a single item from any source.

    Supports two modes:
    1. Adapter payload: {"source": "slack", "payload": {...}}
    2. Raw shorthand: {"content": "text", "source": "raw", "user": "alice"}
    """
    pipeline = request.app.state.ingest_pipeline
    rate_limiter = request.app.state.ingest_rate_limiter
    auth_ctx = request.state.auth  # Set by auth middleware

    # Rate limit
    allowed, headers = rate_limiter.check(auth_ctx.key_id, req.source)
    if not allowed:
        raise HTTPException(429, "Rate limit exceeded", headers=headers)

    # Build payload
    if req.content is not None and req.payload is None:
        # Raw shorthand mode
        payload = {
            "content": req.content,
            "user": req.user,
            "channel": req.channel,
            "type": req.type or "general",
            "tags": req.tags,
        }
        source = "raw"
    else:
        payload = req.payload or {}
        source = req.source

    # Get adapter
    adapter = _get_adapter(source, request)

    # Determine project (request > key default)
    project = req.project or auth_ctx.project

    # Run pipeline
    result = pipeline.ingest(
        adapter=adapter,
        payload=payload,
        project=project,
        dedup_mode=req.dedup_mode,
        enrich=req.enrich,
        extra_tags=req.tags,
    )

    # Map result to HTTP status
    status_code = _result_status_code(result.status)
    return IngestResponse(
        status=result.status,
        memory_id=result.memory_id,
        source=source,
        enriched=result.enriched,
        dedup_check="unique" if result.memory_id else result.status,
        duplicate_of=result.duplicate_of,
        similarity=result.similarity,
    ), status_code  # Note: actual status mapping in route decorator


@router.post("/batch")
async def ingest_batch(req: BatchIngestRequest, request: Request) -> BatchIngestResponse:
    """Batch ingestion — up to 100 items per request."""
    MAX_BATCH = request.app.state.ingest_config.get("batch_max", 100)

    if len(req.items) > MAX_BATCH:
        raise HTTPException(400, f"Batch size exceeds maximum of {MAX_BATCH}")
    if not req.items:
        raise HTTPException(400, "Batch items list is empty")

    pipeline = request.app.state.ingest_pipeline
    auth_ctx = request.state.auth

    adapter = _get_adapter(req.source, request)
    project = req.project or auth_ctx.project

    results = []
    ingested = 0
    skipped = 0
    failed = 0

    for i, item in enumerate(req.items):
        result = pipeline.ingest(
            adapter=adapter,
            payload=item,
            project=project,
            dedup_mode=req.dedup_mode,
            enrich=req.enrich,
        )
        item_result = BatchItemResult(
            index=i,
            status=result.status,
            memory_id=result.memory_id,
            duplicate_of=result.duplicate_of,
            error=result.error,
        )
        results.append(item_result)

        if result.status == "ingested":
            ingested += 1
        elif result.status in ("duplicate_skipped", "duplicate_merged"):
            skipped += 1
        elif result.status in ("failed", "duplicate_rejected"):
            failed += 1

    # Use 207 Multi-Status if mixed results
    return BatchIngestResponse(
        total=len(req.items),
        ingested=ingested,
        duplicates_skipped=skipped,
        failed=failed,
        results=results,
    )


@router.post("/webhook/{adapter_name}")
async def ingest_webhook(adapter_name: str, request: Request):
    """Adapter-specific webhook endpoint with platform signature verification.

    Handles:
    - Slack Events API (including URL verification challenge)
    - Telegram Bot API updates
    - GitHub/GitLab push webhooks
    """
    body = await request.body()
    headers = dict(request.headers)

    # Step 1: Get adapter + verify signature
    adapter = _get_adapter(adapter_name, request)
    if not adapter.verify(headers, body):
        raise HTTPException(401, "Webhook signature verification failed")

    # Step 2: API key from query param (webhooks can't set custom headers)
    api_key = request.query_params.get("key", "")
    if not api_key:
        # Also check Authorization header as fallback
        auth_header = headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            api_key = auth_header[7:]
    if not api_key:
        raise HTTPException(401, "API key required")

    # Validate key (this sets request.state.auth)
    # ... auth validation logic ...

    payload = json.loads(body)

    # Handle Slack URL verification
    if adapter_name == "slack":
        from ..ingest.adapters.slack import SlackAdapter
        if SlackAdapter.is_url_verification(payload):
            return {"challenge": payload.get("challenge", "")}
        if SlackAdapter.is_bot_message(payload):
            return {"status": "ignored", "reason": "bot_message"}

    # Run pipeline
    pipeline = request.app.state.ingest_pipeline
    project = request.state.auth.project

    result = pipeline.ingest(
        adapter=adapter,
        payload=payload,
        project=project,
    )

    status_code = 201 if result.status == "ingested" else 200
    return IngestResponse(
        status=result.status,
        memory_id=result.memory_id,
        source=adapter_name,
        enriched=result.enriched,
        dedup_check="unique" if result.memory_id else result.status,
        duplicate_of=result.duplicate_of,
    )


@router.get("/status/{tracking_id}")
async def ingest_status(tracking_id: str, request: Request):
    """Check status of a queued ingestion item (async mode only)."""
    queue = request.app.state.ingest_queue
    if queue is None:
        raise HTTPException(404, "Queue mode is not enabled")

    item = queue.get_status(tracking_id)
    if item is None:
        raise HTTPException(404, f"Tracking ID not found: {tracking_id}")

    return {
        "tracking_id": tracking_id,
        "status": item.status,
        "result": item.result,
    }


# ── Helpers ──────────────────────────────────────────────────

def _get_adapter(name: str, request: Request) -> "SourceAdapter":
    """Get adapter instance with secrets from app config."""
    from ..ingest.adapters import get_adapter

    secrets = request.app.state.adapter_secrets.get(name, {})
    try:
        return get_adapter(name, **secrets)
    except ValueError:
        raise HTTPException(400, f"Unknown source adapter: {name}")


def _result_status_code(status: str) -> int:
    return {
        "ingested": 201,
        "duplicate_rejected": 409,
        "duplicate_skipped": 200,
        "duplicate_merged": 200,
        "failed": 400,
        "queued": 202,
    }.get(status, 200)
```

### 10.2 Endpoint Summary

| Method | Path | Description | Auth | Status Codes |
|--------|------|-------------|------|-------------|
| `POST` | `/ingest` | Single-item ingestion | Bearer token | 201, 200, 400, 401, 403, 409, 429 |
| `POST` | `/ingest/batch` | Batch ingestion (≤100 items) | Bearer token | 207, 200, 400, 401, 429 |
| `POST` | `/ingest/webhook/slack` | Slack Events API webhook | Query param `?key=` | 200, 201, 401, 429 |
| `POST` | `/ingest/webhook/telegram` | Telegram Bot API webhook | Query param `?key=` | 200, 201, 401, 429 |
| `POST` | `/ingest/webhook/git` | GitHub/GitLab push webhook | Query param `?key=` | 200, 201, 401, 429 |
| `GET` | `/ingest/status/{id}` | Queue item status (async mode) | Bearer token | 200, 404 |

---

## 11. Authentication

### 11.1 Ingest API Key Model

Ingestion keys extend the existing key management system (`server/routes/keys.py`) with a new `ingest` scope:

```python
# Extended key model (stored in existing keys table)
{
    "key": "lore_ingest_sk_abc123...",
    "name": "slack-engineering",
    "scopes": ["ingest"],              # Key can ONLY be used for ingestion
    "allowed_sources": ["slack"],      # Optional: restrict to specific adapters
    "project": "engineering",          # Optional: auto-assign to this project
    "rate_limit": 100,                 # Optional: per-key rate limit override
    "org_id": "org_123",
    "created_at": "2026-03-06T...",
}
```

### 11.2 Ingest Key Validation

```python
# src/lore/ingest/auth.py

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class IngestAuthContext:
    """Authentication context for ingestion requests."""
    key_id: str
    org_id: str
    project: Optional[str]            # Auto-assign project
    allowed_sources: Optional[List[str]]  # None = all sources allowed
    rate_limit: Optional[int]         # Per-key rate limit override


def validate_ingest_key(
    api_key: str,
    source: str,
    key_store,  # Existing key validation infrastructure
) -> IngestAuthContext:
    """Validate API key for ingestion.

    Checks:
    1. Key exists and is not revoked
    2. Key has "ingest" scope
    3. Key is authorized for the requested source adapter

    Raises HTTPException on failure.
    """
    # Delegate to existing auth infrastructure
    key_data = key_store.validate(api_key)
    if key_data is None:
        raise HTTPException(401, "Invalid API key")

    if "ingest" not in key_data.get("scopes", []):
        raise HTTPException(403, "Key does not have ingest scope")

    allowed = key_data.get("allowed_sources")
    if allowed and source not in allowed:
        raise HTTPException(403, f"Key not authorized for source: {source}")

    return IngestAuthContext(
        key_id=key_data["id"],
        org_id=key_data["org_id"],
        project=key_data.get("project"),
        allowed_sources=allowed,
        rate_limit=key_data.get("rate_limit"),
    )
```

### 11.3 Key Management CLI Extension

```
# Create an ingest-scoped key
lore keys create --name "slack-engineering" --scopes ingest --allowed-sources slack --project engineering

# Create a key for all sources
lore keys create --name "bulk-import" --scopes ingest --rate-limit 500

# List ingest keys
lore keys list --scope ingest
```

---

## 12. Source Tracking

### 12.1 Metadata Structure

Every ingested memory carries source metadata in `memory.metadata["source_info"]`:

```python
{
    "adapter": "slack",                          # Source adapter name
    "channel": "#engineering",                   # Source-specific location
    "user": "alice@company.com",                 # Original author
    "original_timestamp": "2026-03-06T14:30:00Z",
    "ingested_at": "2026-03-06T14:30:05Z",      # When Lore received it
    "source_message_id": "1709734200.123456",    # Platform-unique ID
    "raw_format": "slack_mrkdwn",                # Format before normalization
}
```

### 12.2 Multi-Source Attribution (Merge Mode)

When `dedup_mode=merge`, the duplicate's additional source is appended:

```python
memory.metadata["additional_sources"] = [
    {
        "adapter": "telegram",
        "channel": "Engineering Chat",
        "user": "alice_tg",
        "original_timestamp": "2026-03-06T14:31:00Z",
        "ingested_at": "2026-03-06T14:31:05Z",
        "source_message_id": "12345",
        "raw_format": "plain_text",
    }
]
```

### 12.3 Querying by Source

Source metadata is stored in the existing `metadata` JSONB field. Queries can filter by source using the existing metadata search capabilities:

- **SQLite:** JSON extract in WHERE clause: `json_extract(metadata, '$.source_info.adapter') = 'slack'`
- **Postgres:** JSONB operator: `metadata->'source_info'->>'adapter' = 'slack'`

No new Store methods are needed for V1. If source-filtered queries become common, a dedicated `list_by_source()` method can be added later.

---

## 13. Auto-Enrichment Integration

### 13.1 How Ingestion Triggers the Enrichment Pipeline

The ingestion pipeline does NOT directly call F6, F9, or F2. Instead, it delegates to `lore.remember()`, which already orchestrates:

1. **F9 Classification** — if `classify=True` on the Lore instance
2. **F6 Enrichment** — if `enrichment=True` on the Lore instance
3. **F2 Fact Extraction** — if `fact_extraction=True` on the Lore instance
4. **F1 Knowledge Graph** — if `knowledge_graph=True` on the Lore instance

This means ingestion gets enrichment "for free" — any memory stored via ingestion goes through the same pipeline as a manually remembered memory.

### 13.2 Enrichment Control

```python
# Per-request control:
POST /ingest
{
    "content": "...",
    "enrich": false  # Skip enrichment for this specific item
}

# Server-level control:
LORE_INGEST_AUTO_ENRICH=true   # Enable by default
LORE_ENRICHMENT_ENABLED=true    # Must also be enabled on the Lore instance
```

When `enrich=false` is passed in the request, the ingestion pipeline could skip enrichment by temporarily disabling it. However, since `lore.remember()` handles enrichment internally, the simplest V1 approach is:

- If `enrich=false`, the pipeline sets a flag but does NOT prevent `remember()` from enriching.
- **Alternative (recommended for V1):** Document that `enrich` controls whether the ingest response reports enrichment status. Actual enrichment behavior follows the server's Lore configuration. This avoids adding per-call enrichment toggling to the `remember()` API.

**Future enhancement:** Add an `enrich` parameter to `lore.remember()` for per-call enrichment control.

### 13.3 Enrichment Failure Handling

Consistent with existing behavior: enrichment failures are logged as warnings and never block memory storage. The `IngestResult.enriched` field reports whether enrichment ran successfully.

---

## 14. MCP Tool Integration

### 14.1 `ingest` Tool (added to `mcp/server.py`)

```python
@mcp.tool(description="""Ingest content into Lore with source tracking.

Unlike remember(), ingest tracks where content came from (source, user, channel)
and runs deduplication. Use this when importing content from external systems
or when source attribution matters.

Examples:
- ingest(content="Team decided to use PostgreSQL", source="slack", user="alice", channel="#architecture")
- ingest(content="Fix: use connection pooling for DB", source="manual", user="bob")
""")
def ingest(
    content: str,
    source: str = "mcp",
    user: Optional[str] = None,
    channel: Optional[str] = None,
    type: str = "general",
    tags: Optional[str] = None,
    project: Optional[str] = None,
) -> str:
    """Ingest content with source tracking and deduplication."""
    lore = _get_lore()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    source_info = {
        "adapter": source,
        "user": user,
        "channel": channel,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "raw_format": "plain_text",
    }

    # Use remember() directly — dedup via embedding similarity
    # (MCP tool doesn't need full pipeline; source tracking is the key addition)
    try:
        memory_id = lore.remember(
            content=content,
            type=type,
            tier="long",
            tags=tag_list,
            metadata={"source_info": source_info},
            source=source,
            project=project,
        )
        return f"Ingested as memory {memory_id} (source: {source})"
    except Exception as e:
        return f"Error: {e}"
```

**Design note:** The MCP `ingest` tool is intentionally simpler than the REST endpoint. It uses `lore.remember()` directly with source tracking metadata, without the full adapter pipeline (no webhook verification, no adapter normalization). This is appropriate because MCP tool calls come from trusted AI agents, not external webhooks.

---

## 15. CLI Integration

### 15.1 `ingest` Subcommand (added to `cli.py`)

```python
# In build_parser():
ingest_p = sub.add_parser(
    "ingest",
    help="Ingest content from external sources with source tracking",
)
ingest_p.add_argument("content", nargs="?", help="Text to ingest (or use --file)")
ingest_p.add_argument("--source", required=True, help="Source identifier (e.g., slack, telegram, manual)")
ingest_p.add_argument("--file", "-f", help="Path to file for bulk import (JSON or newline text)")
ingest_p.add_argument("--user", help="Author attribution")
ingest_p.add_argument("--channel", help="Source channel/location")
ingest_p.add_argument("--type", default="general", help="Memory type")
ingest_p.add_argument("--tags", help="Comma-separated tags")
ingest_p.add_argument("--project", help="Project scope")
ingest_p.add_argument("--dedup-mode", default="reject", choices=["reject", "merge", "skip", "allow"])
ingest_p.add_argument("--no-enrich", action="store_true", help="Skip enrichment")


def cmd_ingest(args):
    lore_instance = _get_lore(args.db)
    tag_list = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []

    if args.file:
        _ingest_file(lore_instance, args)
    elif args.content:
        _ingest_single(lore_instance, args, tag_list)
    else:
        print("Error: provide content or --file", file=sys.stderr)
        sys.exit(1)

    lore_instance.close()


def _ingest_single(lore, args, tags):
    source_info = {
        "adapter": args.source,
        "user": args.user,
        "channel": args.channel,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "raw_format": "plain_text",
    }
    mid = lore.remember(
        content=args.content,
        type=args.type,
        tier="long",
        tags=tags,
        metadata={"source_info": source_info},
        source=args.source,
        project=args.project,
    )
    print(f"Ingested: {mid}")


def _ingest_file(lore, args):
    """Bulk import from file — auto-detects format."""
    import json as _json
    from pathlib import Path

    path = Path(args.file)
    if not path.exists():
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    raw = path.read_text(encoding="utf-8")

    # Try JSON first
    try:
        data = _json.loads(raw)
        if isinstance(data, list):
            items = data
        else:
            items = [data]
    except _json.JSONDecodeError:
        # Treat as newline-delimited text
        items = [{"content": line.strip()} for line in raw.splitlines() if line.strip()]

    ingested = 0
    skipped = 0
    failed = 0

    for i, item in enumerate(items):
        content = _extract_content(item, args.source)
        if not content:
            failed += 1
            continue

        source_info = {
            "adapter": args.source,
            "user": item.get("user") or args.user,
            "channel": item.get("channel") or args.channel,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "source_message_id": item.get("message_id") or item.get("ts") or item.get("sha"),
            "raw_format": "plain_text",
        }

        try:
            mid = lore.remember(
                content=content,
                type=item.get("type") or args.type,
                tier="long",
                metadata={"source_info": source_info},
                source=args.source,
                project=args.project,
            )
            ingested += 1
        except Exception as e:
            print(f"  Item {i}: failed — {e}", file=sys.stderr)
            failed += 1

    print(f"Batch complete: {ingested} ingested, {skipped} skipped, {failed} failed")


def _extract_content(item: dict, source: str) -> str:
    """Extract text content from an item dict, handling different source formats."""
    # Direct content field
    if "content" in item:
        return item["content"]
    # Slack export format
    if "text" in item:
        return item["text"]
    # Git commit format
    if "message" in item:
        return item["message"]
    return ""
```

---

## 16. Configuration

### 16.1 Environment Variables

```bash
# ── Feature toggle ──
LORE_INGEST_ENABLED=true                    # Enable ingestion endpoints (default: false)

# ── Queue ──
LORE_INGEST_QUEUE_ENABLED=false             # Enable async queue mode (default: false)
LORE_INGEST_QUEUE_SIZE=1000                 # Max queue depth
LORE_INGEST_QUEUE_WORKERS=2                 # Background worker count

# ── Rate limiting ──
LORE_INGEST_RATE_LIMIT_PER_KEY=100          # Requests/min per API key
LORE_INGEST_RATE_LIMIT_PER_ADAPTER=200      # Requests/min per adapter
LORE_INGEST_RATE_LIMIT_GLOBAL=1000          # Requests/min total

# ── Deduplication ──
LORE_INGEST_DEDUP_MODE=reject               # Default: reject | merge | skip | allow
LORE_INGEST_DEDUP_THRESHOLD=0.95            # Cosine similarity threshold

# ── Content limits ──
LORE_INGEST_BATCH_MAX=100                   # Max items per batch request
LORE_INGEST_MAX_CONTENT_LENGTH=10000        # Max content chars

# ── Enrichment ──
LORE_INGEST_AUTO_ENRICH=true                # Auto-enrich on ingest (requires LORE_ENRICHMENT_ENABLED)

# ── Adapter secrets ──
LORE_SLACK_SIGNING_SECRET=                  # Slack app signing secret
LORE_TELEGRAM_BOT_TOKEN=                    # Telegram bot token
LORE_GIT_WEBHOOK_SECRET=                    # GitHub/GitLab webhook secret
```

### 16.2 Server Startup — Adapter Secret Resolution

```python
# In server/app.py lifespan or startup event:

def _resolve_adapter_secrets() -> dict:
    """Resolve adapter secrets from environment variables."""
    import os
    secrets = {}

    slack_secret = os.environ.get("LORE_SLACK_SIGNING_SECRET")
    if slack_secret:
        secrets["slack"] = {"signing_secret": slack_secret}

    telegram_token = os.environ.get("LORE_TELEGRAM_BOT_TOKEN")
    if telegram_token:
        secrets["telegram"] = {"bot_token": telegram_token}

    git_secret = os.environ.get("LORE_GIT_WEBHOOK_SECRET")
    if git_secret:
        secrets["git"] = {"webhook_secret": git_secret}

    # Raw adapter has no secrets
    secrets["raw"] = {}

    return secrets
```

---

## 17. Error Handling

| Scenario | HTTP Status | Response |
|----------|-------------|----------|
| Unknown source adapter | 400 | `{"detail": "Unknown source adapter: foo"}` |
| Webhook signature invalid | 401 | `{"detail": "Webhook signature verification failed"}` |
| API key missing | 401 | `{"detail": "API key required"}` |
| API key invalid/revoked | 401 | `{"detail": "Invalid API key"}` |
| Key lacks `ingest` scope | 403 | `{"detail": "Key does not have ingest scope"}` |
| Key not authorized for source | 403 | `{"detail": "Key not authorized for source: slack"}` |
| Empty content after normalization | 400 | `{"detail": "Content is empty after normalization"}` |
| Content exceeds max length | — | Truncate silently, log warning, continue |
| Duplicate (reject mode) | 409 | `{"status": "duplicate_rejected", "duplicate_of": "...", "similarity": 0.97}` |
| Rate limit exceeded | 429 | `{"detail": "Rate limit exceeded"}` + rate limit headers + `Retry-After` |
| Queue full | 503 | `{"detail": "Ingestion queue is full"}` |
| Batch exceeds max size | 400 | `{"detail": "Batch size exceeds maximum of 100"}` |
| Batch partial failures | 207 | Multi-Status with per-item results |
| Enrichment fails during ingest | — | Warning logged, memory saved without enrichment |
| Slack URL verification | 200 | `{"challenge": "..."}` (no memory created) |
| Slack bot message | 200 | `{"status": "ignored", "reason": "bot_message"}` |

---

## 18. Testing Strategy

### 18.1 Unit Tests — `tests/test_ingest.py`

**Adapter Tests (per adapter):**

```python
class TestSlackAdapter:
    def test_normalize_basic_message(self):
        """Parses standard Slack event payload into NormalizedMessage."""

    def test_normalize_strips_mrkdwn(self):
        """Removes *bold*, _italic_, ~strike~, ```code```, <@mentions>, <#channels>."""

    def test_verify_valid_signature(self):
        """Accepts correctly signed request (HMAC-SHA256)."""

    def test_verify_invalid_signature(self):
        """Rejects tampered payload."""

    def test_verify_replay_attack(self):
        """Rejects timestamp > 5 minutes old."""

    def test_is_url_verification(self):
        """Detects Slack URL verification challenge payload."""

    def test_is_bot_message(self):
        """Detects bot_message subtype and bot_id."""

    def test_normalize_empty_event(self):
        """Handles missing event fields gracefully."""


class TestTelegramAdapter:
    def test_normalize_text_message(self):
        """Parses standard Telegram message update."""

    def test_normalize_with_entities(self):
        """Strips HTML entities from formatted messages."""

    def test_verify_valid_token(self):
        """Accepts correct X-Telegram-Bot-Api-Secret-Token."""

    def test_verify_invalid_token(self):
        """Rejects wrong token."""

    def test_normalize_group_message(self):
        """Extracts chat title for group messages."""


class TestGitAdapter:
    def test_normalize_github_push(self):
        """Parses GitHub push webhook with multiple commits."""

    def test_normalize_single_commit(self):
        """Parses simple {message, author, sha} format."""

    def test_verify_github_signature(self):
        """Validates X-Hub-Signature-256 header."""

    def test_verify_no_secret_configured(self):
        """Passes verification when no secret is set."""

    def test_sets_code_type_and_git_tag(self):
        """memory_type='code' and tags=['git-commit']."""


class TestRawAdapter:
    def test_normalize_passthrough(self):
        """Passes content, user, channel, tags through."""

    def test_verify_always_true(self):
        """No verification for raw adapter."""

    def test_normalize_with_type_override(self):
        """Respects type field from payload."""
```

**Normalization Tests:**

```python
class TestNormalization:
    def test_strip_slack_mrkdwn_bold_italic(self):
    def test_strip_slack_user_mentions(self):
    def test_strip_slack_channel_references(self):
    def test_strip_slack_urls_with_labels(self):
    def test_strip_telegram_html_tags(self):
    def test_strip_telegram_markdown(self):
    def test_normalize_git_commit_strips_diffstat(self):
    def test_collapse_whitespace(self):
    def test_strip_invisible_chars(self):
    def test_truncate_max_length(self):
    def test_empty_string_returns_empty(self):
    def test_none_handling(self):
```

**Deduplication Tests:**

```python
class TestDeduplicator:
    def test_exact_source_id_match(self):
        """Detects duplicate by source_message_id + adapter name."""

    def test_exact_id_different_adapter_not_duplicate(self):
        """Same source_message_id from different adapters is NOT a duplicate."""

    def test_content_similarity_above_threshold(self):
        """Similarity > 0.95 flagged as duplicate."""

    def test_content_similarity_below_threshold(self):
        """Similarity < 0.95 is NOT a duplicate."""

    def test_no_source_id_skips_exact_match(self):
        """Falls through to content similarity when no source_message_id."""

    def test_empty_content_not_duplicate(self):
        """Empty content returns not duplicate (no embedding)."""

    def test_dedup_mode_reject(self):
    def test_dedup_mode_skip(self):
    def test_dedup_mode_merge(self):
    def test_dedup_mode_allow(self):
```

**Pipeline Tests:**

```python
class TestIngestionPipeline:
    def test_full_pipeline_success(self):
        """normalize → dedup → remember → IngestResult with memory_id."""

    def test_pipeline_empty_content(self):
        """Returns failed status when content is empty after normalization."""

    def test_pipeline_duplicate_rejected(self):
        """Returns duplicate_rejected when dedup mode is reject."""

    def test_pipeline_duplicate_skipped(self):
        """Returns duplicate_skipped when dedup mode is skip."""

    def test_pipeline_source_info_stored(self):
        """Verifies source_info metadata on the stored memory."""

    def test_pipeline_tags_merged(self):
        """Adapter tags + extra tags combined."""

    def test_pipeline_batch(self):
        """Batch processes multiple items with per-item results."""

    def test_pipeline_enrichment_failure_doesnt_block(self):
        """Memory saved even if enrichment fails (via remember() behavior)."""
```

### 18.2 Integration Tests — `tests/test_ingest_api.py`

```python
class TestIngestEndpoint:
    def test_post_ingest_raw_shorthand(self):
        """POST /ingest with content field → 201 with memory_id."""

    def test_post_ingest_with_adapter_payload(self):
        """POST /ingest with source=slack, payload={event: ...} → 201."""

    def test_post_ingest_unauthenticated(self):
        """Missing API key → 401."""

    def test_post_ingest_wrong_scope(self):
        """Key without ingest scope → 403."""

    def test_post_ingest_source_not_allowed(self):
        """Key with allowed_sources=["slack"], source="telegram" → 403."""

    def test_post_ingest_duplicate_reject(self):
        """Ingest same content twice → second returns 409."""

    def test_post_ingest_rate_limited(self):
        """Exceed rate limit → 429 with headers."""

    def test_post_ingest_unknown_adapter(self):
        """source="unknown" → 400."""


class TestBatchEndpoint:
    def test_batch_success(self):
        """POST /ingest/batch with 3 items → 207 with per-item results."""

    def test_batch_exceeds_max(self):
        """101 items → 400."""

    def test_batch_empty(self):
        """Empty items list → 400."""

    def test_batch_partial_failure(self):
        """Mix of success and failure → 207 with correct counts."""

    def test_batch_dedup_skip(self):
        """Batch with dedup_mode=skip → duplicates counted as skipped."""


class TestWebhookEndpoints:
    def test_slack_url_verification(self):
        """Slack challenge request → 200 with challenge response."""

    def test_slack_valid_signature(self):
        """Correctly signed Slack event → 201."""

    def test_slack_invalid_signature(self):
        """Tampered Slack event → 401."""

    def test_slack_bot_message_ignored(self):
        """Bot message → 200 with ignored status."""

    def test_slack_replay_attack(self):
        """Old timestamp → 401."""

    def test_telegram_valid_token(self):
        """Correct secret token → 201."""

    def test_telegram_invalid_token(self):
        """Wrong secret token → 401."""

    def test_git_valid_signature(self):
        """Correct X-Hub-Signature-256 → 201."""

    def test_git_invalid_signature(self):
        """Wrong signature → 401."""

    def test_webhook_no_api_key(self):
        """No ?key= param and no Authorization header → 401."""


class TestRateLimiting:
    def test_per_key_rate_limit(self):
        """Exceeding per-key limit → 429 for that key only."""

    def test_rate_limit_headers_present(self):
        """X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset in response."""

    def test_retry_after_header(self):
        """Retry-After header present on 429."""


class TestQueueMode:
    def test_queue_returns_202(self):
        """When queue enabled, POST /ingest → 202 with tracking_id."""

    def test_queue_status_endpoint(self):
        """GET /ingest/status/{id} → processing status."""

    def test_queue_full_returns_503(self):
        """Full queue → 503."""

    def test_queue_disabled_returns_404(self):
        """GET /ingest/status when queue disabled → 404."""
```

### 18.3 CLI Tests — `tests/test_ingest_cli.py`

```python
class TestIngestCLI:
    def test_ingest_single_content(self):
        """lore ingest "content" --source manual → prints memory ID."""

    def test_ingest_from_json_file(self):
        """lore ingest --source raw --file data.json → batch import."""

    def test_ingest_from_text_file(self):
        """lore ingest --source manual --file notes.txt → one memory per line."""

    def test_ingest_missing_source(self):
        """No --source → error."""

    def test_ingest_missing_content_and_file(self):
        """No content and no --file → error."""

    def test_ingest_file_not_found(self):
        """--file nonexistent.json → error."""

    def test_ingest_source_info_in_metadata(self):
        """Verify source_info stored in memory.metadata."""
```

### 18.4 Edge Case Tests

```python
class TestEdgeCases:
    def test_unicode_content_normalized(self):
        """Content with zero-width chars, BOM, etc. cleaned up."""

    def test_content_at_max_length(self):
        """Exactly 10000 chars → accepted without truncation."""

    def test_content_over_max_length(self):
        """10001 chars → truncated to 10000."""

    def test_slack_mrkdwn_nested_formatting(self):
        """*bold _italic_* → properly stripped."""

    def test_telegram_empty_message(self):
        """Telegram update with no text → empty content → failed."""

    def test_git_multiple_commits(self):
        """Push with 5 commits → messages joined with double newline."""

    def test_concurrent_dedup_race(self):
        """Two simultaneous ingests of same content → at most one stored."""
```

---

## 19. Implementation Stories / Task Sequence

### Story 1: Adapter Foundation (base + raw + normalize)
- `src/lore/ingest/__init__.py`
- `src/lore/ingest/adapters/__init__.py` (registry)
- `src/lore/ingest/adapters/base.py` (SourceAdapter, NormalizedMessage)
- `src/lore/ingest/adapters/raw.py` (RawAdapter)
- `src/lore/ingest/normalize.py` (all normalization functions)
- Tests: RawAdapter, normalization

### Story 2: Slack Adapter
- `src/lore/ingest/adapters/slack.py`
- Tests: Slack normalization, mrkdwn stripping, HMAC verification, replay protection, URL verification, bot message filtering

### Story 3: Telegram + Git Adapters
- `src/lore/ingest/adapters/telegram.py`
- `src/lore/ingest/adapters/git.py`
- Tests: Telegram normalization + token verification, Git normalization + GitHub signature verification

### Story 4: Deduplication
- `src/lore/ingest/dedup.py`
- Tests: exact ID match, content similarity, all dedup modes

### Story 5: Ingestion Pipeline
- `src/lore/ingest/pipeline.py`
- Tests: full pipeline flow, empty content, duplicates, source_info storage, batch

### Story 6: REST Endpoints
- `src/lore/server/routes/ingest.py`
- `src/lore/ingest/auth.py`
- `src/lore/ingest/rate_limit.py`
- Tests: POST /ingest, POST /ingest/batch, webhook endpoints, auth, rate limiting

### Story 7: Queue (P1)
- `src/lore/ingest/queue.py`
- Queue integration in routes
- Tests: 202 response, status endpoint, queue full

### Story 8: MCP Tool + CLI
- Addition to `src/lore/mcp/server.py` (ingest tool)
- Addition to `src/lore/cli.py` (ingest subcommand + file import)
- Tests: MCP tool, CLI single + file import

---

## 20. Performance Considerations

| Concern | Mitigation |
|---------|-----------|
| Enrichment latency per ingested item | Enrichment is optional. Queue mode decouples acceptance from processing. |
| Embedding computation per item | Already fast (~5-10ms with local ONNX model). |
| Dedup source ID scan | Limited to 100 recent memories. O(1) index planned for future. |
| Dedup similarity search | Limited to top-5 candidates. Uses existing store.search() which is already optimized. |
| Burst ingestion from webhooks | Rate limiting (3 levels) + queue (configurable max_size) protect against thundering herd. |
| Batch endpoint memory usage | 100-item cap. Items processed sequentially (no parallel enrichment). |
| Queue growth during sustained burst | Max queue size (default 1000). Returns 503 when full. |
| Normalization regex cost | Pre-compiled patterns. Max content length (10k chars) bounds regex execution time. |

---

## 21. Interaction with Existing Systems

| System | Interaction | Changes Required |
|--------|-------------|-----------------|
| **Memory Model** (`types.py`) | Source tracking in `metadata["source_info"]` | None — uses existing `metadata` JSONB field |
| **Store** (`store/base.py`) | Standard `save()`, `list()`, `update()`, `get()` | None — no new methods needed |
| **EmbeddingRouter** (`embed/`) | `embed()` for dedup content similarity | None — existing interface |
| **Enrichment** (F6) | Via `lore.remember()` | None |
| **Classification** (F9) | Via `lore.remember()` | None |
| **Fact Extraction** (F2) | Via `lore.remember()` | None |
| **Knowledge Graph** (F1) | Via `lore.remember()` | None |
| **Redaction** (`redact/`) | Via `lore.remember()` | None |
| **Auth** (`server/auth.py`) | New `ingest` scope for API keys | Add `"ingest"` to valid scopes |
| **Rate Limiting** (`server/rate_limit.py`) | Wraps existing `RateLimitBackend` | None — uses existing interface |
| **Server** (`server/app.py`) | Register ingest router | Add `router` mount + config |
| **MCP Server** (`mcp/server.py`) | New `ingest` tool | Add tool registration |
| **CLI** (`cli.py`) | New `ingest` subcommand | Add subparser + handler |

**Key insight:** The ingestion layer is additive. It adds new code (`src/lore/ingest/`) and new routes, but modifies very little existing code. The only changes to existing files are:
1. `server/app.py` — mount ingest router
2. `server/auth.py` — add `"ingest"` to valid scopes
3. `mcp/server.py` — add `ingest` tool
4. `cli.py` — add `ingest` subcommand
