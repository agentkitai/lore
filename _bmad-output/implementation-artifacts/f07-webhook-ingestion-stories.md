# User Stories: F7 — Webhook / Multi-Source Ingestion

**Feature:** Webhook / Multi-Source Ingestion
**Version:** v0.6.0 ("Open Brain")
**Author:** SM Agent
**Date:** 2026-03-06
**PRD:** `_bmad-output/planning-artifacts/f07-webhook-ingestion-prd.md`
**Architecture:** `_bmad-output/implementation-artifacts/f07-webhook-ingestion-architecture.md`

---

## Story Map

```
F7-S1: Adapter Base + Raw Adapter + Normalization (foundation)
  |
  +---> F7-S2: Slack Adapter (parallel with S3, S4)
  +---> F7-S3: Telegram Adapter (parallel with S2, S4)
  +---> F7-S4: Git Adapter (parallel with S2, S3)
  |
F7-S5: Deduplication Engine (depends on S1)
  |
F7-S6: Ingestion Pipeline Orchestrator (depends on S1, S5)
  |
  +---> F7-S7: REST Endpoints + Auth + Rate Limiting (depends on S6)
  +---> F7-S8: Batch Ingestion Endpoint (depends on S6, S7)
  +---> F7-S9: MCP Tool + CLI Subcommand (depends on S6)
  |
F7-S10: Async Queue Mode (depends on S7)
```

---

## F7-S1: Adapter Base, Raw Adapter, and Content Normalization

**As a** developer building the ingestion system,
**I want** a base adapter class, raw text adapter, and content normalization module,
**so that** all source adapters share a common interface and content is cleaned consistently.

**Estimate:** M

**Dependencies:** None (foundation story)

### Acceptance Criteria

**AC1: NormalizedMessage dataclass**
- **Given** any source adapter processes a payload
- **When** the adapter produces output
- **Then** it returns a `NormalizedMessage` with fields: `content` (str), `user` (Optional[str]), `channel` (Optional[str]), `timestamp` (Optional[str]), `source_message_id` (Optional[str]), `raw_format` (str, default "plain_text"), `memory_type` (str, default "general"), `tags` (Optional[List[str]])

**AC2: SourceAdapter abstract base class**
- **Given** a new adapter is being created
- **When** it extends `SourceAdapter`
- **Then** it must implement `normalize(payload: dict) -> NormalizedMessage` and may override `verify(request_headers: dict, request_body: bytes) -> bool` (default returns True)

**AC3: RawAdapter normalization**
- **Given** a payload `{"content": "Some text", "user": "alice", "channel": "manual", "type": "lesson", "tags": ["important"]}`
- **When** the `RawAdapter.normalize()` is called
- **Then** it returns a `NormalizedMessage` with content="Some text", user="alice", channel="manual", memory_type="lesson", tags=["important"], raw_format="plain_text"

**AC4: Content normalization — whitespace and invisible chars**
- **Given** text with excessive whitespace (3+ consecutive newlines) and zero-width Unicode characters (U+200B, U+FEFF, etc.)
- **When** `normalize_content(text, "plain_text")` is called
- **Then** multiple blank lines are collapsed to double-newline, horizontal whitespace is collapsed, and zero-width characters are removed

**AC5: Content normalization — length limit**
- **Given** text longer than 10,000 characters
- **When** `normalize_content()` is called
- **Then** the output is truncated to 10,000 characters with leading/trailing whitespace stripped

**AC6: Adapter registry**
- **Given** the adapter registry in `ingest/adapters/__init__.py`
- **When** `get_adapter("raw")` is called
- **Then** it returns a `RawAdapter` instance
- **And when** `get_adapter("unknown")` is called
- **Then** it raises `ValueError("Unknown source adapter: unknown")`

### Implementation Notes

- Files: `src/lore/ingest/__init__.py`, `src/lore/ingest/adapters/__init__.py`, `src/lore/ingest/adapters/base.py`, `src/lore/ingest/adapters/raw.py`, `src/lore/ingest/normalize.py`
- Use `dataclasses.dataclass` for `NormalizedMessage`
- Use `abc.ABC` and `@abstractmethod` for `SourceAdapter.normalize()`
- Normalization uses `re` (stdlib only)

---

## F7-S2: Slack Source Adapter

**As a** team using Slack,
**I want** Lore to accept Slack webhook payloads and normalize Slack mrkdwn to plain text,
**so that** conversations from Slack channels flow into Lore automatically.

**Estimate:** M

**Dependencies:** F7-S1 (Adapter Base + Normalization)

### Acceptance Criteria

**AC1: Slack mrkdwn stripping**
- **Given** Slack text with formatting: `"<@U123ABC> said *bold* and _italic_ in <#C456|general>"`
- **When** `normalize_content(text, "slack_mrkdwn")` is called
- **Then** the result is `"@U123ABC said bold and italic in #general"`

**AC2: Slack URL handling**
- **Given** Slack text `"Check <https://example.com|this link> and <https://plain.com>"`
- **When** normalized
- **Then** the result is `"Check this link and https://plain.com"`

**AC3: Slack code block stripping**
- **Given** text with `` ```python\nprint("hi")\n``` `` and inline `` `code` ``
- **When** normalized
- **Then** formatting delimiters are removed, code content preserved

**AC4: Slack payload normalization**
- **Given** a Slack Events API payload with `{"event": {"text": "...", "user": "U123", "channel": "C456", "ts": "1709734200.123456"}}`
- **When** `SlackAdapter.normalize()` is called
- **Then** it returns `NormalizedMessage` with content stripped of mrkdwn, user="U123", channel="C456", timestamp="1709734200.123456", source_message_id="1709734200.123456", raw_format="slack_mrkdwn"

**AC5: Slack webhook signature verification**
- **Given** a request with valid `X-Slack-Request-Timestamp` and `X-Slack-Signature` headers
- **When** `SlackAdapter.verify()` is called with the correct signing secret
- **Then** it returns True
- **And given** a tampered body or invalid signature
- **Then** it returns False

**AC6: Slack replay protection**
- **Given** a request with `X-Slack-Request-Timestamp` older than 5 minutes
- **When** `SlackAdapter.verify()` is called
- **Then** it returns False (replay attack prevention)

**AC7: Slack URL verification challenge**
- **Given** a Slack payload with `{"type": "url_verification", "challenge": "abc123"}`
- **When** `SlackAdapter.is_url_verification()` is called
- **Then** it returns True (route handler responds with challenge, no memory created)

**AC8: Slack bot message filtering**
- **Given** a Slack payload with `{"event": {"subtype": "bot_message"}}` or `{"event": {"bot_id": "B123"}}`
- **When** `SlackAdapter.is_bot_message()` is called
- **Then** it returns True (payload is ignored to prevent feedback loops)

### Implementation Notes

- File: `src/lore/ingest/adapters/slack.py`
- `_strip_slack_mrkdwn()` goes in `normalize.py`
- Signing secret verification uses `hmac.compare_digest()` for constant-time comparison
- Constructor takes `signing_secret: str` parameter

---

## F7-S3: Telegram Source Adapter

**As a** user forwarding messages via Telegram bot,
**I want** Lore to accept Telegram Bot API webhook updates and strip HTML/Markdown formatting,
**so that** my Telegram conversations are ingested as clean memories.

**Estimate:** S

**Dependencies:** F7-S1 (Adapter Base + Normalization)

### Acceptance Criteria

**AC1: Telegram HTML stripping**
- **Given** Telegram text with HTML entities: `"<b>bold</b> and <a href=\"https://x.com\">link</a>"`
- **When** `normalize_content(text, "telegram_html")` is called
- **Then** the result is `"bold and link"`

**AC2: Telegram Markdown stripping**
- **Given** text with `**bold**`, `__italic__`, and `` ```code``` ``
- **When** `normalize_content(text, "telegram_markdown")` is called
- **Then** formatting is removed, content preserved

**AC3: Telegram payload normalization**
- **Given** a Telegram update payload with `{"message": {"text": "hello", "from": {"username": "alice", "id": 123}, "chat": {"title": "My Group", "id": -456}, "date": 1709734200, "message_id": 789}}`
- **When** `TelegramAdapter.normalize()` is called
- **Then** it returns `NormalizedMessage` with content="hello", user="alice", channel="My Group", timestamp as ISO 8601 UTC, source_message_id="789"

**AC4: Telegram user fallback**
- **Given** a Telegram user without `username` but with `id: 12345`
- **When** normalized
- **Then** user is set to `"12345"`

**AC5: Telegram webhook verification**
- **Given** a request with `X-Telegram-Bot-Api-Secret-Token` header matching SHA-256(bot_token)[:32]
- **When** `TelegramAdapter.verify()` is called
- **Then** it returns True
- **And given** a mismatched token
- **Then** it returns False

### Implementation Notes

- File: `src/lore/ingest/adapters/telegram.py`
- `_strip_telegram_formatting()` goes in `normalize.py`
- Constructor takes `bot_token: str`, derives `secret_token` via `hashlib.sha256(bot_token.encode()).hexdigest()[:32]`
- Uses `hmac.compare_digest()` for token comparison

---

## F7-S4: Git Commit Hook Adapter

**As a** developer using GitHub/GitLab webhooks,
**I want** Lore to accept git push webhook payloads and extract commit messages,
**so that** development decisions captured in commits are stored as memories.

**Estimate:** S

**Dependencies:** F7-S1 (Adapter Base + Normalization)

### Acceptance Criteria

**AC1: Git commit message normalization**
- **Given** a commit message with diff-stat lines (`" 3 files changed, 10 insertions(+)"`), diff hunks (`"@@...@@"`), and `+/- `lines
- **When** `normalize_content(text, "git_commit")` is called
- **Then** diff artifacts are stripped; subject line, body, and trailer lines (Signed-off-by, Co-authored-by) are preserved

**AC2: GitHub webhook payload normalization**
- **Given** a GitHub push webhook payload with `{"commits": [{"message": "feat: add auth", "author": {"email": "alice@co.com"}, "id": "abc123", "timestamp": "2026-03-06T14:30:00Z"}], "repository": {"full_name": "org/repo"}}`
- **When** `GitAdapter.normalize()` is called
- **Then** it returns `NormalizedMessage` with content from commit messages, user="alice@co.com", channel="org/repo", source_message_id="abc123", memory_type="code", tags=["git-commit"], raw_format="git_commit"

**AC3: Multi-commit payload**
- **Given** a push webhook with 3 commits
- **When** normalized
- **Then** all commit messages are joined with double-newline separator

**AC4: Simple commit format**
- **Given** a simple payload `{"message": "fix bug", "author": "bob", "sha": "def456", "repo": "my-project"}`
- **When** `GitAdapter.normalize()` is called
- **Then** it handles the flat format correctly (user="bob", channel="my-project", source_message_id="def456")

**AC5: GitHub webhook signature verification**
- **Given** a request with `X-Hub-Signature-256` header containing `sha256=<hmac>`
- **When** `GitAdapter.verify()` is called with a matching webhook secret
- **Then** it returns True
- **And given** an invalid signature or missing `sha256=` prefix
- **Then** it returns False

**AC6: No secret configured**
- **Given** a `GitAdapter` initialized without `webhook_secret`
- **When** `verify()` is called
- **Then** it returns True (verification skipped)

### Implementation Notes

- File: `src/lore/ingest/adapters/git.py`
- `_normalize_git_message()` goes in `normalize.py`
- Constructor takes `webhook_secret: Optional[str] = None`
- Uses `hmac.compare_digest()` for signature comparison

---

## F7-S5: Deduplication Engine

**As a** system operator,
**I want** ingested content to be checked for duplicates before storage,
**so that** webhook retries and near-identical content don't pollute the memory store.

**Estimate:** M

**Dependencies:** F7-S1 (NormalizedMessage dataclass)

### Acceptance Criteria

**AC1: Exact source ID deduplication**
- **Given** an existing memory with `metadata.source_info.source_message_id = "1709734200.123456"` and `adapter = "slack"`
- **When** a new message with the same `source_message_id` and adapter arrives
- **Then** `Deduplicator.check()` returns `DedupResult(is_duplicate=True, strategy="exact_id", similarity=1.0, duplicate_of=<existing_id>)`

**AC2: Cross-adapter no false match**
- **Given** a Slack message with source_message_id="123" already stored
- **When** a Telegram message with source_message_id="123" is checked
- **Then** it is NOT flagged as duplicate (different adapter)

**AC3: Content similarity deduplication**
- **Given** an existing memory and new content with cosine similarity >= 0.95 to it
- **When** `Deduplicator.check()` runs content similarity (strategy 2)
- **Then** it returns `DedupResult(is_duplicate=True, strategy="content_similarity")` with the similarity score

**AC4: Below threshold passes**
- **Given** new content with similarity 0.90 to the nearest existing memory (threshold=0.95)
- **When** checked
- **Then** `DedupResult(is_duplicate=False)` is returned

**AC5: Empty content skips similarity**
- **Given** a `NormalizedMessage` with empty/whitespace-only content and no source_message_id
- **When** checked
- **Then** it returns `DedupResult(is_duplicate=False)` without computing embeddings

**AC6: DedupResult dataclass**
- **Given** any dedup check
- **When** it completes
- **Then** the result has fields: `is_duplicate` (bool), `duplicate_of` (Optional[str]), `similarity` (float), `strategy` (str: "exact_id" | "content_similarity" | "")

### Implementation Notes

- File: `src/lore/ingest/dedup.py`
- Constructor: `Deduplicator(store, embedder, threshold=0.95)`
- Exact ID lookup scans recent memories via `store.list()` checking `metadata.source_info`
- Content similarity uses existing `store.search()` with embedding

---

## F7-S6: Ingestion Pipeline Orchestrator

**As a** developer,
**I want** a pipeline that orchestrates normalization, dedup, and storage for ingested content,
**so that** all ingestion paths (REST, MCP, CLI) share the same processing logic.

**Estimate:** M

**Dependencies:** F7-S1 (Adapters), F7-S5 (Deduplication)

### Acceptance Criteria

**AC1: Successful ingestion**
- **Given** a valid payload and a source adapter
- **When** `IngestionPipeline.ingest(adapter, payload, project="test")` is called
- **Then** it normalizes content, runs dedup, delegates to `lore.remember()`, and returns `IngestResult(status="ingested", memory_id=<id>, enriched=<bool>)`

**AC2: Source metadata stored**
- **Given** a successful ingestion
- **When** the resulting memory is retrieved
- **Then** `memory.metadata["source_info"]` contains: `adapter`, `channel`, `user`, `original_timestamp`, `ingested_at` (ISO 8601), `source_message_id`, `raw_format`
- **And** `memory.source` equals the adapter name

**AC3: Dedup mode "reject"**
- **Given** duplicate content detected and dedup_mode="reject" (default)
- **When** `ingest()` is called
- **Then** it returns `IngestResult(status="duplicate_rejected", duplicate_of=<id>)` without calling `lore.remember()`

**AC4: Dedup mode "skip"**
- **Given** duplicate content detected and dedup_mode="skip"
- **When** `ingest()` is called
- **Then** it returns `IngestResult(status="duplicate_skipped")` without storing

**AC5: Dedup mode "merge"**
- **Given** duplicate content detected and dedup_mode="merge"
- **When** `ingest()` is called
- **Then** new source_info is appended to the existing memory's `metadata["additional_sources"]` list

**AC6: Dedup mode "allow"**
- **Given** dedup_mode="allow"
- **When** `ingest()` is called
- **Then** dedup is skipped entirely and content is stored regardless

**AC7: Empty content rejection**
- **Given** a payload that normalizes to empty/whitespace-only content
- **When** `ingest()` is called
- **Then** it returns `IngestResult(status="failed", error="Content is empty after normalization")`

**AC8: Storage failure handling**
- **Given** `lore.remember()` raises an exception
- **When** `ingest()` catches it
- **Then** it returns `IngestResult(status="failed", error=<message>)` and logs the error

**AC9: Batch ingestion**
- **Given** a list of N payloads
- **When** `ingest_batch(items, adapter)` is called
- **Then** it returns a list of N `IngestResult` objects, one per item, with independent success/failure status

**AC10: Ingested memories default to long-term tier**
- **Given** any successful ingestion
- **When** the memory is stored
- **Then** `tier="long"` is set (ingested content is long-term by default)

### Implementation Notes

- File: `src/lore/ingest/pipeline.py`
- `IngestionPipeline(lore, deduplicator, default_dedup_mode="reject", auto_enrich=True)`
- Delegates to `lore.remember()` — does NOT directly call F6/F9/F2
- `IngestResult` dataclass: status, memory_id, duplicate_of, similarity, dedup_strategy, enriched, tracking_id, error

---

## F7-S7: REST Ingestion Endpoints with Auth and Rate Limiting

**As a** system integrator,
**I want** REST endpoints for single-item ingestion and adapter-specific webhooks with API key auth and rate limiting,
**so that** external systems can securely push content into Lore via HTTP.

**Estimate:** L

**Dependencies:** F7-S6 (Ingestion Pipeline), F7-S2/S3/S4 (at least one adapter for webhook routes)

### Acceptance Criteria

**AC1: POST /ingest — single item**
- **Given** a valid request `{"source": "raw", "payload": {"content": "test"}, "project": "p1"}`
- **When** `POST /ingest` is called with `Authorization: Bearer <valid-key>`
- **Then** it returns 201 with `{"status": "ingested", "memory_id": "...", "source": "raw", "enriched": <bool>, "dedup_check": "unique"}`

**AC2: POST /ingest — raw shorthand**
- **Given** `{"content": "text", "source": "raw", "user": "alice"}`
- **When** posted to `/ingest`
- **Then** it is treated as raw adapter input (shorthand for `payload` wrapping)

**AC3: Webhook endpoints**
- **Given** platform-specific webhook URLs
- **When** `POST /ingest/webhook/slack`, `POST /ingest/webhook/telegram`, or `POST /ingest/webhook/git` is called
- **Then** the corresponding adapter's `verify()` runs BEFORE payload processing
- **And** API key auth is checked (via `?key=` query param or Authorization header)

**AC4: Slack URL verification**
- **Given** a Slack URL verification challenge request to `/ingest/webhook/slack`
- **When** the payload has `{"type": "url_verification", "challenge": "abc123"}`
- **Then** the endpoint returns 200 with `{"challenge": "abc123"}` and no memory is created

**AC5: Authentication required**
- **Given** a request without an API key or with an invalid key
- **When** any ingest endpoint is called
- **Then** it returns 401 Unauthorized

**AC6: Scope enforcement**
- **Given** an API key without `ingest` scope
- **When** used on ingest endpoints
- **Then** it returns 403 Forbidden

**AC7: Source restriction**
- **Given** an API key with `allowed_sources: ["slack"]`
- **When** used to ingest with `source: "telegram"`
- **Then** it returns 403 Forbidden

**AC8: Rate limiting**
- **Given** per-key rate limit of 100 req/min
- **When** the 101st request arrives within the minute window
- **Then** it returns 429 with headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`, and `Retry-After`

**AC9: Duplicate rejection response**
- **Given** dedup_mode="reject" and duplicate detected
- **When** the endpoint processes it
- **Then** it returns 409 Conflict with `{"status": "duplicate_rejected", "duplicate_of": "...", "similarity": ..., "strategy": "..."}`

**AC10: Invalid adapter**
- **Given** `{"source": "unknown_adapter"}`
- **When** posted to `/ingest`
- **Then** it returns 400 Bad Request with `"Unknown source adapter: unknown_adapter"`

**AC11: Webhook signature failure**
- **Given** an invalid webhook signature on `/ingest/webhook/slack`
- **When** verification fails
- **Then** it returns 401 with `"Webhook signature verification failed"`

### Implementation Notes

- File: `src/lore/server/routes/ingest.py`
- Auth: extend existing key management with `ingest` scope and `allowed_sources`
- Rate limiter: `IngestRateLimiter` wraps existing `RateLimitBackend` with 3 levels (per-key, per-adapter, global)
- Files: `src/lore/ingest/auth.py`, `src/lore/ingest/rate_limit.py`
- Server config: `LORE_INGEST_ENABLED` (default false), `LORE_INGEST_RATE_LIMIT`, adapter secrets via env vars

---

## F7-S8: Batch Ingestion Endpoint

**As a** system integrator performing bulk imports,
**I want** a batch ingestion endpoint that accepts multiple items in one request,
**so that** I can efficiently import large volumes of content without per-item HTTP overhead.

**Estimate:** S

**Dependencies:** F7-S7 (REST Endpoints + Auth)

### Acceptance Criteria

**AC1: Batch ingestion**
- **Given** a request with `{"items": [{"content": "A"}, {"content": "B"}], "source": "raw", "project": "p1"}`
- **When** `POST /ingest/batch` is called
- **Then** it returns per-item results: `{"status": "batch_complete", "total": 2, "ingested": 2, "duplicates_skipped": 0, "failed": 0, "results": [...]}`

**AC2: Item limit**
- **Given** a batch request with more than 100 items
- **When** posted
- **Then** it returns 400 Bad Request with message about exceeding batch limit

**AC3: Partial failures**
- **Given** a batch where item 0 succeeds, item 1 is a duplicate (mode=skip), and item 2 has empty content
- **When** processed
- **Then** response includes per-item results with individual statuses: "ingested", "duplicate_skipped", "failed"
- **And** HTTP status is 207 Multi-Status (for partial failures) or 200 (all succeed)

**AC4: Batch rate limiting**
- **Given** a batch of N items
- **When** rate limits are checked
- **Then** the batch counts as N individual requests against the per-key rate limit

**AC5: Batch dedup and enrich options**
- **Given** batch-level `dedup_mode` and `enrich` options
- **When** the batch is processed
- **Then** these options apply to all items in the batch

### Implementation Notes

- Added to `src/lore/server/routes/ingest.py`
- Uses `IngestionPipeline.ingest_batch()` internally
- Max items configurable via `LORE_INGEST_BATCH_MAX` (default 100)

---

## F7-S9: MCP Tool and CLI Subcommand

**As a** user or AI agent,
**I want** an `ingest` MCP tool and `lore ingest` CLI command,
**so that** I can ingest content with source tracking from any interface, including bulk file import.

**Estimate:** M

**Dependencies:** F7-S6 (Ingestion Pipeline)

### Acceptance Criteria

**AC1: MCP ingest tool — basic**
- **Given** calling the MCP `ingest` tool with `content="lesson learned", source="mcp", user="agent"`
- **When** the tool executes
- **Then** it stores the memory with `metadata.source_info` containing adapter="mcp", user="agent", ingested_at timestamp
- **And** returns `"Ingested as memory <id> (source: mcp)"`

**AC2: MCP ingest tool — parameters**
- **Given** the MCP `ingest` tool
- **When** its schema is inspected
- **Then** it accepts: `content` (required), `source` (default "mcp"), `user`, `channel`, `type` (default "general"), `tags` (comma-separated), `project`

**AC3: CLI single item ingest**
- **Given** running `lore ingest "Some knowledge" --source manual --user alice --project p1`
- **When** the command executes
- **Then** a memory is created with source_info.adapter="manual", source_info.user="alice", project="p1"

**AC4: CLI file import — JSON array**
- **Given** a file `data.json` containing `[{"content": "A", "user": "alice"}, {"content": "B", "user": "bob"}]`
- **When** `lore ingest --source raw --file data.json` is run
- **Then** both items are ingested with per-item results printed

**AC5: CLI file import — newline text**
- **Given** a file `notes.txt` with one memory per line
- **When** `lore ingest --source raw --file notes.txt` is run
- **Then** each non-empty line is ingested as a separate memory

**AC6: CLI dedup and enrich options**
- **Given** `--dedup-mode skip --no-enrich`
- **When** passed to `lore ingest`
- **Then** dedup mode is set to "skip" and enrichment is disabled for this import

**AC7: CLI error handling**
- **Given** `--file nonexistent.json`
- **When** the CLI runs
- **Then** it exits with a clear error message about the file not found

**AC8: CLI format auto-detection**
- **Given** a file with `.json` extension
- **When** `--source slack --file slack-export.json` is run
- **Then** the CLI detects Slack export format (array of message objects) and processes each message through the Slack adapter

### Implementation Notes

- MCP tool: add to `src/lore/mcp/server.py`
- CLI: add `ingest` subcommand to `src/lore/cli.py`
- CLI arguments: `content` (positional, mutually exclusive with `--file`), `--source`, `--file`, `--user`, `--channel`, `--type`, `--tags`, `--project`, `--dedup-mode`, `--no-enrich`, `--db`
- File format detection: JSON array vs newline text based on content parsing

---

## F7-S10: Async Ingestion Queue

**As a** system operator handling burst ingestion,
**I want** an optional async queue mode that accepts items immediately and processes them in the background,
**so that** webhook sources get fast responses even during high-volume ingestion.

**Estimate:** M

**Dependencies:** F7-S7 (REST Endpoints)

### Acceptance Criteria

**AC1: Queue mode enabled**
- **Given** `LORE_INGEST_QUEUE_ENABLED=true` in server config
- **When** `POST /ingest` is called
- **Then** it returns 202 Accepted with `{"status": "queued", "tracking_id": "<id>"}` immediately

**AC2: Background processing**
- **Given** items enqueued
- **When** queue workers process them
- **Then** each item goes through the full ingestion pipeline (normalize, dedup, remember)

**AC3: Queue status endpoint**
- **Given** a tracking_id from a queued ingestion
- **When** `GET /ingest/status/<tracking_id>` is called
- **Then** it returns the current status: "queued", "processing", "ingested", "failed", etc.

**AC4: Queue full**
- **Given** the queue has reached max_size (default 1000)
- **When** a new item is enqueued
- **Then** it returns 503 Service Unavailable with `"Ingestion queue is full"`

**AC5: Configurable workers**
- **Given** `LORE_INGEST_QUEUE_WORKERS=4`
- **When** the server starts with queue enabled
- **Then** 4 worker tasks process the queue concurrently

**AC6: Sync mode default**
- **Given** `LORE_INGEST_QUEUE_ENABLED=false` (default)
- **When** `POST /ingest` is called
- **Then** processing is synchronous (returns 201/409/etc. as before)

### Implementation Notes

- File: `src/lore/ingest/queue.py`
- Uses `asyncio.Queue` — in-process only, no external broker
- `IngestionQueue(max_size=1000, workers=2)`
- Tracking IDs stored in an in-memory dict (lost on restart — documented limitation)
- Config: `LORE_INGEST_QUEUE_ENABLED`, `LORE_INGEST_QUEUE_SIZE`, `LORE_INGEST_QUEUE_WORKERS`

---

## Summary

| Story | Title | Estimate | Dependencies |
|-------|-------|----------|-------------|
| F7-S1 | Adapter Base, Raw Adapter, and Content Normalization | M | None |
| F7-S2 | Slack Source Adapter | M | F7-S1 |
| F7-S3 | Telegram Source Adapter | S | F7-S1 |
| F7-S4 | Git Commit Hook Adapter | S | F7-S1 |
| F7-S5 | Deduplication Engine | M | F7-S1 |
| F7-S6 | Ingestion Pipeline Orchestrator | M | F7-S1, F7-S5 |
| F7-S7 | REST Endpoints + Auth + Rate Limiting | L | F7-S6 |
| F7-S8 | Batch Ingestion Endpoint | S | F7-S7 |
| F7-S9 | MCP Tool + CLI Subcommand | M | F7-S6 |
| F7-S10 | Async Ingestion Queue | M | F7-S7 |

**Total: 10 stories (3S + 5M + 1L = ~21 story points using S=1, M=3, L=5)**

### Suggested Sprint Order

**Sprint 1:** F7-S1 (foundation), then F7-S2 + F7-S3 + F7-S4 + F7-S5 in parallel
**Sprint 2:** F7-S6, then F7-S7 + F7-S9 in parallel
**Sprint 3:** F7-S8 + F7-S10 in parallel
