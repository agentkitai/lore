# PRD: F7 — Webhook / Multi-Source Ingestion

**Feature:** Webhook / Multi-Source Ingestion
**Version:** v0.6.0 ("Open Brain")
**Status:** Draft
**Author:** John (PM)
**Date:** 2026-03-06
**Dependencies:** F6 (Metadata Enrichment), F9 (Dialog Classification), F2 (Fact Extraction) — for auto-enrichment pipeline on ingest. These are soft dependencies: ingestion works without them, but auto-enrichment requires them.
**Dependents:** None

---

## 1. Problem Statement

Lore currently only accepts memories through direct SDK calls (`remember()`), the MCP `remember` tool, or the CLI `lore remember` command. All three require manual, one-at-a-time input from an AI agent or user. There is no way to automatically feed knowledge into Lore from external systems — Slack conversations, Telegram chats, Git commit messages, or any webhook-capable service.

Competitive platforms (Mem0, Zep) offer ingestion pipelines that pull from multiple sources automatically. Lore needs webhook endpoints and source adapters so that knowledge flows into the system continuously, not just when an agent explicitly calls `remember()`.

## 2. Goals

1. **REST ingestion endpoint** — `POST /ingest` accepts content from any source, runs embedding + enrichment pipeline, and stores the resulting memory.
2. **Source adapters** — Built-in adapters for Slack webhooks, Telegram bot forwards, Git commit hooks, and raw text POST. Each adapter normalizes source-specific payloads into a common format.
3. **Source tracking** — Every ingested memory records its source adapter, channel/repo, original timestamp, and user identity.
4. **Batch ingestion** — `POST /ingest/batch` accepts multiple memories in a single request for bulk import scenarios.
5. **Authentication** — API key per source, with separate keys for different adapters/integrations.
6. **Webhook verification** — Validate incoming webhooks using platform-specific signing (Slack signing secret, Telegram token verification).
7. **Rate limiting + queueing** — Protect against burst ingestion with per-source rate limits and an internal queue.
8. **Content normalization** — Strip formatting, extract plain text from Slack mrkdwn, Telegram HTML/Markdown, Git diff patches, etc.
9. **Auto-enrichment on ingest** — Optionally run F6 enrichment, F9 classification, and F2 fact extraction on ingested content.
10. **Deduplication** — Detect near-duplicate content on ingest and reject or merge.
11. **MCP tool** — `ingest` tool for manual ingestion with source tracking.
12. **CLI** — `lore ingest` command for bulk import from files.

## 3. Non-Goals

- **Real-time streaming** — WebSocket or SSE-based streaming ingestion. V1 is request/response only.
- **Outbound webhooks** — Notifying external systems when memories are created/updated. This is a separate concern.
- **Source-specific rich features** — Slack thread following, Telegram inline queries, GitHub PR review comments. V1 handles simple message/commit payloads.
- **Custom adapter plugins** — V1 ships with 4 built-in adapters. User-defined adapter registration is a future consideration.
- **OAuth flows** — Source authentication uses API keys and webhook secrets, not OAuth.
- **Message editing/deletion sync** — If a Slack message is edited or deleted after ingestion, Lore does not retroactively update or remove the memory.

## 4. Design

### 4.1 Ingestion Data Model

Every ingested memory carries source metadata in `memory.metadata["source_info"]`:

```python
# Stored in memory.metadata["source_info"]
{
    "adapter": "slack",                          # slack | telegram | git | raw
    "channel": "#engineering",                   # channel name, chat name, repo path
    "user": "alice@company.com",                 # original author identity
    "original_timestamp": "2026-03-06T14:30:00Z",  # timestamp from the source system
    "ingested_at": "2026-03-06T14:30:05Z",       # when Lore received it
    "source_message_id": "1709734200.123456",    # platform-specific message ID (for dedup)
    "raw_format": "slack_mrkdwn"                 # original content format before normalization
}
```

**Field definitions:**

| Field | Type | Description |
|-------|------|-------------|
| `adapter` | `str` | Source adapter that processed the payload: `slack`, `telegram`, `git`, `raw` |
| `channel` | `str` | Source channel, group, or repo. Adapter-specific (e.g., Slack channel name, Telegram chat title, Git repo path). |
| `user` | `str` | Original author. Format varies by adapter (email, username, Telegram user ID). |
| `original_timestamp` | `str` | ISO 8601 timestamp from the source system. |
| `ingested_at` | `str` | ISO 8601 timestamp of when Lore received the payload. |
| `source_message_id` | `str` | Platform-specific unique ID for the message/commit. Used for deduplication. |
| `raw_format` | `str` | Original content format: `slack_mrkdwn`, `telegram_html`, `telegram_markdown`, `git_commit`, `plain_text`. |

### 4.2 Source Adapters

Each adapter is responsible for:
1. **Payload parsing** — Extract content, user, channel, timestamp from the source-specific JSON format.
2. **Webhook verification** — Validate the request signature (where applicable).
3. **Content normalization** — Convert source-specific formatting to plain text.

```python
# src/lore/ingest/adapters/base.py

from dataclasses import dataclass
from typing import Optional

@dataclass
class NormalizedMessage:
    """Common format produced by all source adapters."""
    content: str                     # Plain text, formatting stripped
    user: Optional[str] = None
    channel: Optional[str] = None
    timestamp: Optional[str] = None
    source_message_id: Optional[str] = None
    raw_format: str = "plain_text"
    memory_type: str = "general"     # Adapter may suggest a type (e.g., "code" for git)
    tags: list = None                # Adapter may suggest tags

class SourceAdapter:
    """Base class for source adapters."""

    adapter_name: str = "raw"

    def verify(self, request_headers: dict, request_body: bytes) -> bool:
        """Verify webhook signature. Returns True if valid or not applicable."""
        return True

    def normalize(self, payload: dict) -> NormalizedMessage:
        """Parse source payload and return normalized message."""
        raise NotImplementedError
```

#### 4.2.1 Slack Adapter

Parses Slack Events API payloads (`message` events). Verifies using Slack signing secret (HMAC-SHA256 of `v0:timestamp:body`).

```python
class SlackAdapter(SourceAdapter):
    adapter_name = "slack"

    def __init__(self, signing_secret: str):
        self.signing_secret = signing_secret

    def verify(self, request_headers: dict, request_body: bytes) -> bool:
        """Verify Slack request signature using HMAC-SHA256."""
        timestamp = request_headers.get("x-slack-request-timestamp", "")
        signature = request_headers.get("x-slack-signature", "")
        # Verify: hmac_sha256(signing_secret, f"v0:{timestamp}:{body}") == signature
        ...

    def normalize(self, payload: dict) -> NormalizedMessage:
        event = payload.get("event", {})
        return NormalizedMessage(
            content=self._strip_mrkdwn(event.get("text", "")),
            user=event.get("user"),
            channel=event.get("channel"),
            timestamp=event.get("ts"),
            source_message_id=event.get("ts"),  # Slack uses ts as message ID
            raw_format="slack_mrkdwn",
        )

    def _strip_mrkdwn(self, text: str) -> str:
        """Convert Slack mrkdwn to plain text."""
        # Remove <@U123> user mentions -> @username
        # Remove <#C123|channel> -> #channel
        # Remove *bold*, _italic_, ~strikethrough~, ```code blocks```
        ...
```

**Slack-specific behavior:**
- Responds to Slack URL verification challenges (`{"type": "url_verification"}`) automatically.
- Ignores bot messages (`subtype == "bot_message"`) to avoid feedback loops.
- Extracts user display name via Slack user ID (if configured with a bot token).

#### 4.2.2 Telegram Adapter

Parses Telegram Bot API webhook updates. Verifies using the bot token (check that the webhook URL contains the secret token).

```python
class TelegramAdapter(SourceAdapter):
    adapter_name = "telegram"

    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.secret_token = hashlib.sha256(bot_token.encode()).hexdigest()[:32]

    def verify(self, request_headers: dict, request_body: bytes) -> bool:
        """Verify Telegram secret token header."""
        return request_headers.get("x-telegram-bot-api-secret-token") == self.secret_token

    def normalize(self, payload: dict) -> NormalizedMessage:
        message = payload.get("message", {})
        chat = message.get("chat", {})
        user = message.get("from", {})
        return NormalizedMessage(
            content=self._extract_text(message),
            user=user.get("username") or str(user.get("id", "")),
            channel=chat.get("title") or str(chat.get("id", "")),
            timestamp=datetime.fromtimestamp(message.get("date", 0), tz=timezone.utc).isoformat(),
            source_message_id=str(message.get("message_id", "")),
            raw_format="telegram_html" if message.get("entities") else "plain_text",
        )

    def _extract_text(self, message: dict) -> str:
        """Extract plain text from Telegram message, stripping entities."""
        ...
```

#### 4.2.3 Git Commit Hook Adapter

Parses git commit payloads (compatible with GitHub/GitLab webhook format and local `post-commit` hook output).

```python
class GitAdapter(SourceAdapter):
    adapter_name = "git"

    def normalize(self, payload: dict) -> NormalizedMessage:
        # Handles both GitHub webhook format and simple {message, author, sha, repo} format
        commits = payload.get("commits", [payload])
        messages = []
        for commit in commits:
            msg = commit.get("message", "")
            messages.append(msg)

        return NormalizedMessage(
            content="\n\n".join(messages),
            user=commits[0].get("author", {}).get("email") or commits[0].get("author", ""),
            channel=payload.get("repository", {}).get("full_name") or payload.get("repo", ""),
            timestamp=commits[0].get("timestamp") or commits[0].get("date", ""),
            source_message_id=commits[0].get("id") or commits[0].get("sha", ""),
            raw_format="git_commit",
            memory_type="code",
            tags=["git-commit"],
        )
```

#### 4.2.4 Raw Text Adapter

Default adapter for plain text POST. No verification, no normalization beyond basic cleanup.

```python
class RawAdapter(SourceAdapter):
    adapter_name = "raw"

    def normalize(self, payload: dict) -> NormalizedMessage:
        return NormalizedMessage(
            content=payload.get("content", ""),
            user=payload.get("user"),
            channel=payload.get("channel"),
            timestamp=payload.get("timestamp"),
            source_message_id=payload.get("message_id"),
            raw_format="plain_text",
            memory_type=payload.get("type", "general"),
            tags=payload.get("tags"),
        )
```

### 4.3 Content Normalization

A shared normalization module handles common cleanup across all adapters:

```python
# src/lore/ingest/normalize.py

def normalize_content(text: str, format: str = "plain_text") -> str:
    """Normalize content from various source formats to clean plain text.

    Steps:
    1. Strip source-specific formatting (mrkdwn, HTML, etc.)
    2. Collapse excessive whitespace
    3. Remove zero-width characters and other invisible Unicode
    4. Trim to max content length (10,000 chars)
    5. Strip leading/trailing whitespace
    """
    if format == "slack_mrkdwn":
        text = _strip_slack_mrkdwn(text)
    elif format in ("telegram_html", "telegram_markdown"):
        text = _strip_telegram_formatting(text)
    elif format == "git_commit":
        text = _normalize_git_message(text)

    text = _collapse_whitespace(text)
    text = _strip_invisible_chars(text)
    text = text[:10000].strip()
    return text
```

### 4.4 Deduplication

Near-duplicate detection runs before storage. Uses two strategies:

1. **Exact source ID match** — If a memory already exists with the same `source_message_id` from the same adapter, reject immediately. This catches Slack retries and duplicate webhook deliveries.

2. **Content similarity** — Compute embedding for the normalized content, then check cosine similarity against recent memories (last 24 hours, same project). If similarity > 0.95, reject or merge based on config.

```python
# src/lore/ingest/dedup.py

@dataclass
class DedupResult:
    is_duplicate: bool
    duplicate_of: Optional[str] = None  # Memory ID of the existing duplicate
    similarity: float = 0.0
    strategy: str = ""                  # "exact_id" | "content_similarity"

class Deduplicator:
    def __init__(self, store: Store, embedder: Embedder, threshold: float = 0.95):
        self.store = store
        self.embedder = embedder
        self.threshold = threshold

    def check(self, normalized: NormalizedMessage, project: Optional[str] = None) -> DedupResult:
        """Check if content is a near-duplicate of an existing memory."""

        # Strategy 1: Exact source message ID match
        if normalized.source_message_id:
            existing = self._find_by_source_id(
                normalized.source_message_id, normalized.raw_format
            )
            if existing:
                return DedupResult(
                    is_duplicate=True,
                    duplicate_of=existing.id,
                    similarity=1.0,
                    strategy="exact_id",
                )

        # Strategy 2: Content similarity
        embedding = self.embedder.embed(normalized.content)
        similar = self.store.search_similar(
            embedding, limit=5, project=project, min_score=self.threshold
        )
        if similar:
            return DedupResult(
                is_duplicate=True,
                duplicate_of=similar[0].memory.id,
                similarity=similar[0].score,
                strategy="content_similarity",
            )

        return DedupResult(is_duplicate=False)
```

**Dedup behavior is configurable:**

| Mode | Behavior |
|------|----------|
| `reject` (default) | Return 409 Conflict with the duplicate memory ID |
| `merge` | Append source_info to existing memory's metadata (record that same content came from another source) |
| `skip` | Silently accept but don't store; return 200 with `"status": "duplicate_skipped"` |
| `allow` | Disable dedup entirely; store even if duplicate |

### 4.5 REST Endpoints

#### 4.5.1 POST /ingest

Single-item ingestion endpoint.

**Request:**

```http
POST /ingest
Authorization: Bearer <api-key>
Content-Type: application/json

{
    "source": "slack",                    # Required: adapter name
    "payload": { ... },                   # Required: source-specific payload
    "project": "my-project",             # Optional: project scope
    "enrich": true,                       # Optional: run enrichment pipeline (default: server config)
    "dedup_mode": "reject"               # Optional: reject | merge | skip | allow
}
```

**Or, for raw text (shorthand):**

```http
POST /ingest
Authorization: Bearer <api-key>
Content-Type: application/json

{
    "content": "Some knowledge to remember",
    "source": "raw",
    "user": "alice",
    "channel": "manual-import",
    "type": "lesson",
    "tags": ["important"],
    "project": "my-project"
}
```

**Response (success):**

```json
{
    "status": "ingested",
    "memory_id": "01HQXYZ...",
    "source": "slack",
    "enriched": true,
    "dedup_check": "unique"
}
```

**Response (duplicate):**

```json
{
    "status": "duplicate_rejected",
    "duplicate_of": "01HQABC...",
    "similarity": 0.97,
    "strategy": "content_similarity"
}
```

**Status codes:**

| Code | Meaning |
|------|---------|
| 201 | Memory created successfully |
| 200 | Duplicate skipped (mode=skip) or Slack URL verification response |
| 400 | Invalid payload, missing required fields, unknown adapter |
| 401 | Missing or invalid API key |
| 403 | API key not authorized for this source adapter |
| 409 | Duplicate rejected (mode=reject) |
| 429 | Rate limit exceeded |

#### 4.5.2 POST /ingest/batch

Batch ingestion for bulk import.

**Request:**

```http
POST /ingest/batch
Authorization: Bearer <api-key>
Content-Type: application/json

{
    "items": [
        {
            "content": "First memory",
            "source": "raw",
            "user": "alice",
            "type": "lesson"
        },
        {
            "content": "Second memory",
            "source": "raw",
            "user": "bob",
            "type": "code"
        }
    ],
    "project": "my-project",
    "enrich": false,
    "dedup_mode": "skip"
}
```

**Response:**

```json
{
    "status": "batch_complete",
    "total": 50,
    "ingested": 47,
    "duplicates_skipped": 2,
    "failed": 1,
    "results": [
        {"index": 0, "status": "ingested", "memory_id": "01HQ..."},
        {"index": 1, "status": "duplicate_skipped", "duplicate_of": "01HQ..."},
        {"index": 2, "status": "failed", "error": "Content is empty"}
    ]
}
```

**Limits:**
- Maximum 100 items per batch request.
- Batch requests count against rate limits as N individual requests.

#### 4.5.3 Webhook Endpoints (Adapter-Specific)

For Slack and Telegram, dedicated webhook endpoints handle platform-specific flows:

```
POST /ingest/webhook/slack     — Handles Slack Events API (including URL verification)
POST /ingest/webhook/telegram  — Handles Telegram Bot API updates
POST /ingest/webhook/git       — Handles GitHub/GitLab push webhooks
```

These endpoints:
- Perform platform-specific signature verification before processing.
- Handle platform handshake flows (Slack URL verification challenge).
- Route to the appropriate adapter internally.
- Use the same API key authentication as `/ingest` (in the query string for webhooks that don't support custom headers: `?key=<api-key>`).

### 4.6 Authentication

#### 4.6.1 API Keys

Each source integration has its own API key. Keys are managed via the existing key management system (`/api/v1/keys`).

```python
# Extended key model
{
    "key": "lore_ingest_sk_abc123...",
    "name": "slack-engineering",
    "scopes": ["ingest"],             # Key can only be used for ingestion
    "allowed_sources": ["slack"],     # Restrict to specific adapters (optional)
    "project": "engineering",         # Auto-assign ingested memories to this project
    "rate_limit": 100,                # Per-minute rate limit for this key
    "created_at": "2026-03-06T..."
}
```

**Key features:**
- Keys are scoped to `ingest` — they cannot read, recall, or delete memories.
- Optional `allowed_sources` restricts which adapters the key can use.
- Optional `project` auto-assigns ingested memories to a project.
- Per-key rate limits override global defaults.

#### 4.6.2 Webhook Verification

Platform-specific verification happens BEFORE API key authentication:

| Platform | Verification Method |
|----------|-------------------|
| Slack | HMAC-SHA256 of `v0:{timestamp}:{body}` using signing secret. Reject if timestamp > 5 min old (replay protection). |
| Telegram | `X-Telegram-Bot-Api-Secret-Token` header matches SHA-256 of bot token (first 32 chars). |
| Git (GitHub) | HMAC-SHA256 of body using webhook secret (`X-Hub-Signature-256` header). |
| Raw | No webhook verification (relies on API key only). |

### 4.7 Rate Limiting + Queue

#### 4.7.1 Rate Limiting

Rate limits are enforced per API key and per source adapter:

| Scope | Default Limit | Configurable |
|-------|--------------|-------------|
| Per API key | 100 requests/minute | Yes, per key |
| Per source adapter | 200 requests/minute | Yes, server config |
| Global | 1000 requests/minute | Yes, server config |
| Batch items | 100 per request | Yes, server config |

Rate limit headers in response:
```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 73
X-RateLimit-Reset: 1709734260
```

#### 4.7.2 Internal Queue

For burst ingestion (e.g., bulk Slack history import), an internal queue decouples request acceptance from processing:

```python
# src/lore/ingest/queue.py

class IngestionQueue:
    """In-process queue for burst ingestion.

    Accepts items immediately (returns 202 Accepted) and processes
    them sequentially in a background task. Uses asyncio.Queue.
    """

    def __init__(self, max_size: int = 1000, workers: int = 2):
        self.queue = asyncio.Queue(maxsize=max_size)
        self.workers = workers

    async def enqueue(self, item: IngestItem) -> str:
        """Add item to queue. Returns a tracking ID."""
        ...

    async def process(self, item: IngestItem) -> None:
        """Process a single queued item: normalize, dedup, embed, enrich, store."""
        ...
```

**Queue behavior:**
- When queue is enabled (server config), `/ingest` returns 202 Accepted with a tracking ID.
- Queue status endpoint: `GET /ingest/status/{tracking_id}` returns processing state.
- When queue is disabled (default for local/dev), processing is synchronous (201/409 responses).
- Queue is in-process only (asyncio.Queue). No external broker (Redis, RabbitMQ) in V1.

### 4.8 Ingestion Pipeline

The full pipeline from incoming request to stored memory:

```
1. Receive request
2. Authenticate (API key)
3. Select adapter (from "source" field or webhook URL)
4. Verify webhook signature (adapter-specific)
5. Parse & normalize content (adapter.normalize())
6. Validate (non-empty content, reasonable length)
7. Deduplication check
8. Create Memory object:
   - content = normalized text
   - source = adapter name
   - metadata.source_info = source tracking data
   - type = adapter-suggested or request-specified
   - tags = adapter-suggested + request-specified
   - project = request-specified or key-default
   - tier = "long" (default for ingested content)
9. Embed (compute embedding via existing EmbeddingRouter)
10. Auto-enrich (if enabled):
    a. F6: metadata enrichment (topics, sentiment, entities, categories)
    b. F9: dialog classification (intent, domain, emotion)
    c. F2: fact extraction + conflict resolution
11. Redact (PII masking + secret blocking)
12. Store memory
13. Return response with memory ID
```

**Order matters:**
- Normalization before embedding (clean text makes better embeddings).
- Dedup before enrichment (don't waste LLM calls on duplicates).
- Enrichment before redaction is intentional — enrichment needs full content for quality extraction. Redaction happens last to ensure stored content is safe.
- Note: This differs from `remember()` which redacts before enrichment. For ingestion, since content comes from external systems (not user-typed), we prioritize enrichment quality. The stored content is always redacted.

### 4.9 Auto-Enrichment on Ingest

When enrichment features are enabled, ingestion automatically runs the enrichment pipeline:

```python
class IngestionPipeline:
    def __init__(
        self,
        lore: Lore,
        enrichment_enabled: bool = False,
        classification_enabled: bool = False,
        fact_extraction_enabled: bool = False,
    ):
        self.lore = lore
        self.enrichment_enabled = enrichment_enabled
        self.classification_enabled = classification_enabled
        self.fact_extraction_enabled = fact_extraction_enabled

    def ingest(self, normalized: NormalizedMessage, project: Optional[str] = None) -> str:
        """Run full ingestion pipeline and return memory ID."""
        # Uses lore.remember() which already handles enrichment/classification/facts
        # The ingestion layer's job is normalization, dedup, and source tracking
        memory_id = self.lore.remember(
            content=normalized.content,
            type=normalized.memory_type,
            tags=normalized.tags or [],
            metadata={"source_info": self._build_source_info(normalized)},
            project=project,
        )
        return memory_id
```

**Key principle:** The ingestion pipeline delegates to `lore.remember()` for the actual storage and enrichment. Ingestion's responsibility is everything upstream: receiving, verifying, normalizing, deduplicating, and source tracking.

### 4.10 Configuration

```python
# Server-side configuration (environment variables)

LORE_INGEST_ENABLED=true                    # Enable ingestion endpoints (default: false)
LORE_INGEST_QUEUE_ENABLED=false             # Enable async queue (default: false)
LORE_INGEST_QUEUE_SIZE=1000                 # Max queue size
LORE_INGEST_QUEUE_WORKERS=2                 # Number of queue workers
LORE_INGEST_RATE_LIMIT=100                  # Global requests/minute
LORE_INGEST_BATCH_MAX=100                   # Max items per batch request
LORE_INGEST_DEDUP_MODE=reject               # Default dedup mode
LORE_INGEST_DEDUP_THRESHOLD=0.95            # Cosine similarity threshold for content dedup
LORE_INGEST_MAX_CONTENT_LENGTH=10000        # Max content length in chars
LORE_INGEST_AUTO_ENRICH=true                # Auto-enrich on ingest (requires F6/F9/F2 enabled)

# Adapter-specific secrets
LORE_SLACK_SIGNING_SECRET=abc123            # Slack app signing secret
LORE_TELEGRAM_BOT_TOKEN=123:ABC            # Telegram bot token
LORE_GIT_WEBHOOK_SECRET=xyz789             # GitHub/GitLab webhook secret
```

### 4.11 MCP Tool: ingest

New MCP tool for manual ingestion with source tracking.

```python
@mcp.tool()
def ingest(
    content: str,
    source: str = "mcp",
    user: Optional[str] = None,
    channel: Optional[str] = None,
    type: str = "general",
    tags: Optional[str] = None,
    project: Optional[str] = None,
) -> str:
    """Ingest content into Lore with source tracking.

    Unlike remember(), ingest tracks where the content came from (source,
    user, channel) and runs deduplication. Use this when importing content
    from external systems or when source attribution matters.

    Args:
        content: The text content to ingest.
        source: Source identifier (e.g., "slack", "telegram", "manual", "mcp").
        user: Who authored the original content.
        channel: Where the content came from (channel name, repo, etc.).
        type: Memory type (general, code, lesson, convention, etc.).
        tags: Comma-separated tags.
        project: Project scope.
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    source_info = {
        "adapter": source,
        "user": user,
        "channel": channel,
        "ingested_at": datetime.utcnow().isoformat() + "Z",
        "raw_format": "plain_text",
    }

    memory_id = lore.remember(
        content=content,
        type=type,
        tags=tag_list,
        metadata={"source_info": source_info},
        source=source,
        project=project,
    )
    return f"Ingested as memory {memory_id} (source: {source})"
```

### 4.12 CLI: lore ingest

```
# Single item
lore ingest "Some knowledge" --source manual --user alice

# From file (one memory per line)
lore ingest --file notes.txt --source manual --user alice

# Bulk import from JSON
lore ingest --source slack --file slack-export.json --project engineering

# Bulk import from JSON with specific format
lore ingest --source telegram --file telegram-dump.json --project personal
```

**CLI arguments:**

| Argument | Description |
|----------|-------------|
| `content` (positional) | Text to ingest (mutually exclusive with `--file`) |
| `--source` | Source identifier (required) |
| `--file` | Path to file for bulk import (JSON array or newline-delimited text) |
| `--user` | Author attribution |
| `--channel` | Source channel/location |
| `--type` | Memory type (default: general) |
| `--tags` | Comma-separated tags |
| `--project` | Project scope |
| `--dedup-mode` | Dedup behavior: reject, merge, skip, allow (default: reject) |
| `--no-enrich` | Skip enrichment even if enabled in config |
| `--db` | Database path (for local store) |

**File format support:**

1. **JSON array** — `[{"content": "...", "user": "...", ...}, ...]`
2. **Newline-delimited text** — Each line is a separate memory.
3. **Slack export JSON** — Standard Slack export format (array of message objects).
4. **Telegram export JSON** — Telegram Desktop export format.

The CLI detects the format automatically based on file content and `--source` flag.

## 5. Module Structure

```
src/lore/ingest/
    __init__.py           # Public API: IngestPipeline, ingest()
    pipeline.py           # IngestionPipeline orchestrator
    normalize.py          # Content normalization utilities
    dedup.py              # Deduplication (exact ID + content similarity)
    queue.py              # IngestionQueue (async, in-process)
    adapters/
        __init__.py       # Adapter registry, get_adapter()
        base.py           # SourceAdapter ABC, NormalizedMessage dataclass
        slack.py          # SlackAdapter
        telegram.py       # TelegramAdapter
        git.py            # GitAdapter
        raw.py            # RawAdapter
```

Server-side additions:
```
src/lore/server/routes/
    ingest.py             # /ingest, /ingest/batch, /ingest/webhook/* endpoints
```

## 6. Dependencies

### 6.1 Required

No new required dependencies. Ingestion uses existing FastAPI, SQLite/Postgres, and embedding infrastructure.

### 6.2 Optional

| Package | Purpose | When Needed |
|---------|---------|-------------|
| `hmac`, `hashlib` | Webhook signature verification | Built-in (stdlib) |
| `asyncio` | Queue workers | Built-in (stdlib) |

### 6.3 Optional dependency pattern

```toml
# pyproject.toml — no new optional deps needed for V1
# Ingestion uses stdlib for crypto and existing FastAPI for endpoints
```

## 7. Error Handling

| Scenario | Behavior |
|----------|----------|
| Unknown source adapter | 400 Bad Request: "Unknown source adapter: {name}" |
| Webhook verification fails | 401 Unauthorized: "Webhook signature verification failed" |
| Empty content after normalization | 400 Bad Request: "Content is empty after normalization" |
| Content exceeds max length | Truncate to max length, log warning, continue |
| Duplicate detected (reject mode) | 409 Conflict with duplicate memory ID |
| Rate limit exceeded | 429 Too Many Requests with rate limit headers and Retry-After |
| Queue full | 503 Service Unavailable: "Ingestion queue is full" |
| Enrichment fails during ingest | Warning logged, memory saved without enrichment (never blocks storage) |
| Batch: partial failures | Return 207 Multi-Status with per-item results |
| API key missing | 401 Unauthorized |
| API key lacks ingest scope | 403 Forbidden: "Key does not have ingest scope" |
| Slack URL verification challenge | 200 with challenge response (no memory created) |

## 8. Performance Considerations

| Concern | Mitigation |
|---------|-----------|
| Enrichment adds latency per ingested item | Enrichment is optional. Queue mode decouples acceptance from processing. |
| Embedding computation per item | Already fast (~10ms with ONNX). Batch endpoint amortizes overhead. |
| Dedup similarity search on every ingest | Search is limited to recent memories (24h window) and top-5 candidates. |
| Burst ingestion from Slack/Telegram | Rate limiting + queue protect against thundering herd. |
| Batch endpoint memory usage | 100-item cap prevents excessive memory use. Items processed sequentially. |
| Queue growth during sustained burst | Max queue size (default 1000). Returns 503 when full. |

## 9. Implementation Plan

### 9.1 Task Breakdown

1. **src/lore/ingest/adapters/base.py** — `SourceAdapter` ABC, `NormalizedMessage` dataclass.
2. **src/lore/ingest/adapters/raw.py** — Raw text adapter.
3. **src/lore/ingest/adapters/slack.py** — Slack adapter with mrkdwn stripping, signing secret verification.
4. **src/lore/ingest/adapters/telegram.py** — Telegram adapter with HTML/Markdown stripping, token verification.
5. **src/lore/ingest/adapters/git.py** — Git commit adapter with commit message normalization.
6. **src/lore/ingest/normalize.py** — Content normalization utilities (shared across adapters).
7. **src/lore/ingest/dedup.py** — Deduplication (exact source ID match + embedding similarity).
8. **src/lore/ingest/pipeline.py** — `IngestionPipeline` orchestrator (normalize -> dedup -> remember).
9. **src/lore/ingest/queue.py** — `IngestionQueue` (asyncio-based, in-process).
10. **src/lore/server/routes/ingest.py** — REST endpoints: `/ingest`, `/ingest/batch`, `/ingest/webhook/*`.
11. **src/lore/mcp/server.py** — New `ingest` tool.
12. **src/lore/cli.py** — `ingest` subcommand with file import support.
13. **tests/test_ingest.py** — Unit tests for adapters, normalization, dedup, pipeline.
14. **tests/test_ingest_api.py** — Integration tests for REST endpoints.

### 9.2 Testing Strategy

- **Unit tests:** Each adapter (normalize + verify), content normalization, deduplication logic, pipeline orchestration.
- **Adapter tests:** Verify real Slack/Telegram/Git payload formats are correctly parsed and normalized.
- **Dedup tests:** Exact ID match, content similarity threshold, all dedup modes (reject/merge/skip/allow).
- **Integration tests:** Full endpoint tests (POST /ingest, POST /ingest/batch) with mocked store.
- **Webhook verification tests:** Valid signatures pass, invalid signatures rejected, replay attacks blocked (Slack timestamp check).
- **Rate limit tests:** Verify rate limiting kicks in at configured thresholds.
- **CLI tests:** File import (JSON, text), single item ingest, error handling.
- **Error path tests:** Empty content, unknown adapter, queue full, enrichment failure.

## 10. Acceptance Criteria

### Must Have (P0)

- [ ] `POST /ingest` endpoint accepts content with source tracking and returns memory ID.
- [ ] Source adapters for Slack, Telegram, Git, and raw text correctly parse their respective payload formats.
- [ ] Content normalization strips Slack mrkdwn, Telegram HTML/Markdown, and Git commit formatting to plain text.
- [ ] Source metadata (`adapter`, `channel`, `user`, `original_timestamp`, `ingested_at`, `source_message_id`) stored in `metadata.source_info`.
- [ ] Webhook verification: Slack signing secret, Telegram token, Git webhook secret all validated before processing.
- [ ] Invalid webhook signatures return 401, not silently accepted.
- [ ] Deduplication: exact source message ID match prevents duplicate storage.
- [ ] Deduplication: content similarity > 0.95 detected and handled per dedup_mode.
- [ ] `POST /ingest/batch` accepts up to 100 items and returns per-item results.
- [ ] API key authentication required on all ingest endpoints.
- [ ] API key scoped to `ingest` cannot read/recall/delete memories.
- [ ] Rate limiting enforced per API key (default 100/min).
- [ ] Rate limit exceeded returns 429 with appropriate headers.
- [ ] MCP `ingest` tool stores memory with source tracking metadata.
- [ ] CLI `lore ingest "content" --source manual` creates memory with source info.
- [ ] CLI `lore ingest --source raw --file data.json` bulk imports from JSON file.
- [ ] Auto-enrichment runs on ingested content when F6/F9/F2 are enabled.
- [ ] Enrichment failure does not block memory storage during ingestion.
- [ ] Slack URL verification challenge handled correctly (returns challenge, no memory created).
- [ ] Bot messages from Slack ignored (no feedback loops).
- [ ] All existing tests pass unchanged (zero regression).

### Should Have (P1)

- [ ] Dedicated webhook endpoints: `/ingest/webhook/slack`, `/ingest/webhook/telegram`, `/ingest/webhook/git`.
- [ ] Per-key `allowed_sources` restriction enforced.
- [ ] Per-key `project` auto-assignment for ingested memories.
- [ ] Async queue mode: returns 202 Accepted with tracking ID.
- [ ] Queue status endpoint: `GET /ingest/status/{tracking_id}`.
- [ ] CLI auto-detects file format (JSON array, newline text, Slack export, Telegram export).
- [ ] Batch response uses 207 Multi-Status for partial failures.
- [ ] Replay protection: Slack timestamp > 5 min old rejected.

### Could Have (P2)

- [ ] `dedup_mode=merge` appends source_info to existing memory.
- [ ] Queue workers configurable (default 2).
- [ ] Adapter-specific content length limits.
- [ ] Metrics/counters for ingested items by source (Prometheus-compatible).
- [ ] Slack user ID resolution to display name (requires bot token with users:read scope).

## 11. Interaction with Existing Systems

### Memory Model (types.py)
No schema changes required. Source tracking uses the existing `metadata` JSONB field (`metadata.source_info`). The `source` field on Memory is set to the adapter name.

### Enrichment Pipeline (F6/F9/F2)
Ingestion delegates to `lore.remember()` which already runs the enrichment pipeline when enabled. No special integration needed — ingestion is just a new front door to `remember()`.

### Redaction Pipeline
Redaction applies to ingested content just as it does for `remember()`. PII and secrets from external sources are masked before storage.

### Store / Embedding
Ingestion uses the existing `Store` and `EmbeddingRouter`. No changes to storage layer.

### Rate Limiting (server/rate_limit.py)
Ingestion extends the existing rate limiting infrastructure with per-key and per-adapter scoping.

### Authentication (server/auth.py)
Ingestion keys use the existing key management system with a new `ingest` scope.

## 12. Future Considerations (Out of Scope)

- **Outbound webhooks** — Notify external systems when memories are created/updated/consolidated.
- **Custom adapter plugins** — User-defined adapters registered via config or code.
- **OAuth integration** — Slack/Telegram OAuth for automatic channel subscription.
- **Message edit/delete sync** — Track edits and deletions from source systems.
- **Real-time streaming** — WebSocket/SSE ingestion for continuous streams.
- **External queue broker** — Redis/RabbitMQ for production-grade queue persistence.
- **Conversation threading** — Ingest entire Slack threads as single memories with context.
- **Attachment handling** — Extract text from uploaded files (PDF, images via OCR).

## 13. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Webhook flood from misconfigured Slack app | High | Rate limiting per key + global. Queue with max size. Drop oldest on overflow. |
| Duplicate content from webhook retries | Medium | Dedup on source_message_id catches exact retries. Content similarity catches near-dupes. |
| PII in Slack/Telegram messages | High | Redaction pipeline runs on all ingested content. Document that ingestion sends content through PII masking. |
| LLM costs from auto-enrichment on high-volume ingestion | Medium | Auto-enrich is optional. Can be disabled per request (`enrich: false`). Rate limiting caps volume. |
| Webhook secret leakage | High | Secrets stored as environment variables, never logged. Verification uses constant-time comparison. |
| Queue data loss on server restart | Low | V1 uses in-process queue (no persistence). Document that queued items are lost on restart. Future: external broker. |
| Adapter payload format changes (Slack/Telegram API updates) | Medium | Adapters are isolated modules, easy to update. Test against real payload samples. |

## 14. Success Metrics

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Ingestion endpoint latency (without enrichment) | < 100ms p95 | Benchmark POST /ingest with raw adapter |
| Ingestion endpoint latency (with enrichment) | < 1s p95 | Benchmark POST /ingest with enrichment enabled |
| Dedup detection accuracy | > 99% for exact ID, > 90% for content similarity | Test with known duplicate datasets |
| Webhook verification: zero false accepts | 100% — invalid signatures always rejected | Test with tampered payloads |
| Batch throughput | > 50 items/sec without enrichment | Benchmark POST /ingest/batch with 100 items |
| Content normalization quality | Readable plain text from all adapters | Manual review of 20 normalized samples per adapter |
| Zero regression when ingestion disabled | All existing tests pass | pytest full suite with ingest disabled (default) |
