# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0] — 2026-03-14 — "Total Recall"

### Added

- **Session Accumulator:** Deterministic auto-snapshot of conversation context. Captures session state at configurable character thresholds (`LORE_SNAPSHOT_THRESHOLD`, default 30K chars) — no LLM required.
- **Auto-Inject Session Context:** Relevant session history injected into every prompt via hooks. Agents get continuity between conversations without calling any tools.
- **v1.0.0 stability:** All 6 epics complete. Production-ready release.

### Changed

- Version bump to 1.0.0 — "Total Recall"
- PyPI classifier updated to `Development Status :: 5 - Production/Stable`
- Comprehensive README rewrite with full feature documentation

## [0.13.0] — 2026-03-14 — "Approval UX"

### Added

- **E6 Approval UX for Discovered Connections:** Review workflow for knowledge graph connections before they become permanent.
  - New MCP tools: `review_digest`, `review_connection`
  - New REST endpoints: `GET /v1/review`, `POST /v1/review/{id}`, `POST /v1/review/bulk`
  - Approve, reject, or skip pending connections — keep your graph clean

## [0.12.1] — 2026-03-14

### Fixed

- Entity detail endpoint — deduplicate connected entities, handle nodes not in graph
- Ghost tooltip fix in graph visualization
- Importance scoring — auto-bump `access_count` on retrieve, making importance emergent from actual usage
- Access tracking improvements
- Default node limit to 100 with total count in status bar
- Label truncation increased to 200 characters

## [0.12.0] — 2026-03-13 — "Brain Surgery"

### Added

- **E3 Pre-Compaction Hook:** Session snapshots saved automatically before context compression. Preserves conversation state that would otherwise be lost.
  - New MCP tool: `save_snapshot`
  - New REST endpoints: `POST /v1/snapshots`, `GET /v1/export/snapshots`
- **E4 Topic Notes:** Auto-generated concept hubs clustering related memories, entities, and facts around recurring themes.
  - New MCP tools: `topics`, `topic_detail`
  - New REST endpoints: `GET /v1/graph/topics`, `GET /v1/graph/topics/{name}`
- **Graph UI enhancements:** Entity detail panels, force-directed layout, search and filtering

### Removed

- **SQLite backend removed.** Lore now requires PostgreSQL with pgvector. This simplifies the codebase and ensures consistent behavior across all deployments.

## [0.11.0] — 2026-03-13 — "Mind's Eye"

### Added

- **E1 Graph Visualization Web UI:** Interactive D3 force-directed graph at `/ui/`. Browse entities, relationships, and topic clusters in the browser. Entity detail panels, zoom, search, and filtering. No install required — served directly by the Lore server.

## [0.10.0] — 2026-03-12

### Added

- **E2 Recent Activity Summary:** Session-aware summary of recent memory activity across projects. New MCP tool: `recent_activity`. New REST endpoint: `GET /v1/recent`. Gives agents continuity between conversations.
- **E5 Export/Snapshot:** Full data export in JSON and Markdown formats. Obsidian-compatible output. Snapshot creation and management for backup and migration.
  - New MCP tools: `export`, `snapshot`, `snapshot_list`
  - New REST endpoints: `POST /v1/export`, `POST /v1/export/snapshots`, `GET /v1/export/snapshots`

## [0.9.5] — 2026-03-10

### Fixed

- Claude Code hook integration — reliable auto-retrieval in all configurations
- Server-side enrichment pipeline fixes

## [0.9.4] — 2026-03-09

### Added

- **`lore serve` command:** Start the HTTP server directly from the CLI
- **Mac install script:** One-command setup (Postgres + pgvector + enrichment + LaunchAgent)

### Fixed

- LaunchAgent plist array bug
- Auto-diagnose server start failures
- Python 3.10+ requirement enforced (auto-install via Homebrew on Mac)
- pgvector build from source when Homebrew targets wrong PG version

## [0.9.3] — 2026-03-09

### Added

- **Retrieval Analytics:** Track hit rate, score distribution, memory utilization, and latency. New REST endpoint: `GET /v1/analytics/retrieval`. Prometheus-compatible metrics export.

## [0.9.2] — 2026-03-08

### Added

- **Cursor setup:** `lore setup cursor` — one-command hook installation with `beforeSubmitPrompt` hook
- **Codex CLI setup:** `lore setup codex` — one-command hook installation with `beforePlan` hook
- Setup guides restructured with Quick Start first

## [0.9.1] — 2026-03-08

### Fixed

- All migrations made idempotent after lessons→memories rename

## [0.9.0] — 2026-03-08 — "Wired In"

### Added

- **Schema migration system:** Automatic database migrations on server startup
- **Setup CLI:** `lore setup claude-code`, `lore setup openclaw` — one-command hook installation for auto-retrieval
- **MCP enrichment:** Server-side enrichment pipeline triggered via MCP tools

### Changed

- Legacy `lessons` schema mapped to `memories` transparently

## [0.8.3] — 2026-03-08

### Added

- **`GET /v1/retrieve` endpoint:** Purpose-built auto-retrieval endpoint for hooks. Semantic search + formatted output designed for prompt injection. Supports XML, Markdown, and raw JSON formats.

## [0.8.2] — 2026-03-07

### Added

- **Conversation auto-ingest:** `lore wrap` CLI command + OpenClaw bridge for automatic memory extraction from conversations

### Fixed

- Client-side cleanup skip for HttpStore (prevents limit=10000 422 error)
- Lint cleanup across test files

## [0.8.1] — 2026-03-07

### Fixed

- Writable model cache directory + graceful dedup fallback
- Enrichment (litellm) included in server Docker image
- Conversation extraction persists to Postgres via MemoryStore

## [0.8.0] — 2026-03-07 — "Conversation Intelligence"

### Added

- **Conversation Auto-Extract:** Accept raw conversation messages and automatically extract salient memories using LLM processing.
  - New `ConversationExtractor` pipeline: validate → concatenate → chunk → extract → dedup → store
  - New SDK method: `lore.add_conversation(messages, user_id=..., session_id=...)`
  - New CLI command: `lore add-conversation --file conversation.json`
  - New MCP tool: `add_conversation`
  - REST API: `POST /v1/conversations` (202 Accepted, async) and `GET /v1/conversations/{job_id}`
- **User-Scoped Recall:** `recall(query, user_id="alice")` filters memories by user
- **Token-Aware Chunking:** Long conversations split into ~8K token chunks with 2-message overlap
- **Cost Estimation:** CLI output includes estimated LLM cost after extraction

### Changed

- `recall()` gains optional `user_id` parameter for memory scoping

## [0.7.0] — 2026-03-07 — "Living Archive"

### Added

- **On This Day:** Query memories from the same month+day across years. New MCP tool: `on_this_day`. New CLI: `lore on-this-day`.
- **Verbatim Recall:** Return original words instead of AI summaries. `--verbatim` flag on CLI, `verbatim` parameter on MCP.
- **Temporal Filters:** Date-range filtering on `recall` — `year`, `month`, `day`, `days_ago`, `hours_ago`, `before`, `after`, and window presets.

## [0.6.0] — 2026-03-06 — "Open Brain"

### Added

- **Knowledge Graph:** Entity and relationship extraction with hop-by-hop graph traversal. New MCP tools: `graph_query`, `entity_map`, `related`.
- **Fact Extraction:** Atomic (subject, predicate, object) triples with conflict detection. New MCP tools: `extract_facts`, `list_facts`, `conflicts`.
- **Memory Consolidation:** Deduplication, topic-based grouping, LLM-powered summarization. New MCP tool: `consolidate`.
- **Memory Tiers:** Working (1h), short-term (7d), long-term (no expiry) with tier-specific decay.
- **Importance Scoring:** Adaptive importance decay based on tier, type, access frequency, and age.
- **Metadata Enrichment:** LLM-powered topics, entities, sentiment, categories. New MCP tool: `enrich`.
- **Webhook Ingestion:** REST ingestion with Slack, Telegram, Git adapters. New MCP tool: `ingest`.
- **Dialog Classification:** Intent, domain, emotion classification. New MCP tool: `classify`.
- **Prompt Export:** Template-based export for LLM injection (XML, ChatML, markdown, raw). New MCP tool: `as_prompt`.
- 13 new MCP tools (7 → 20 total)
- Docker Compose deployment
- Integration test suite
- Performance benchmarks

### Changed

- `recall()` supports graph-enhanced retrieval via `graph_depth` parameter
- `remember()` triggers enrichment, classification, and fact extraction when LLM is configured
- Memory scoring model changed from additive to multiplicative

## [0.5.1]

- Importance scoring foundation, memory tier support.

## [0.5.0]

- Internal improvements and stabilization.

## [0.4.1]

- Bug fixes and minor improvements.

## [0.4.0]

- GitHub sync, freshness checking, additional MCP tools.

## [0.3.0] — 2026-03-04

### Breaking Changes

- **Memory model replaces Lesson model.** Core data type is now `Memory` with single `content` field instead of `problem`/`resolution`. Old aliases preserved as deprecated exports.
- `stats()` returns `MemoryStats` dataclass instead of plain dict.
- TypeScript SDK: `publish()` → `remember()`, `query()` → `recall()`, `list()` → `listMemories()`, `delete()` → `forget()`.

### Added

- `remember(content, ...)` — universal memory storage with type, context, tags, metadata, ttl, source, project, confidence
- `recall(query, ...)` — semantic search with embedding-powered scoring
- `forget(id)` — delete memory by ID
- TTL support — automatic expiry via `ttl` and `expires_at`
- `MemoryStats` dataclass

## [0.2.1]

- SDK hardening: retry, graceful degradation, connection pooling, batching.

## [0.2.0]

- Initial public release with Lesson model, SQLite store, semantic search, PII redaction.
