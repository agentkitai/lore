# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.8.0] — 2026-03-07 — "Conversation Intelligence"

### Added

- **Conversation Auto-Extract:** Accept raw conversation messages and automatically extract salient memories using LLM processing. Zero-intelligence ingestion — the caller provides data, Lore provides intelligence.
  - New `ConversationExtractor` pipeline: validate → concatenate → chunk → extract → dedup → store
  - New SDK method: `lore.add_conversation(messages, user_id=..., session_id=...)`
  - New CLI command: `lore add-conversation --file conversation.json` (also reads from stdin)
  - New MCP tool: `add_conversation` for AI agents to dump conversation context
  - REST API: `POST /v1/conversations` (202 Accepted, async processing) and `GET /v1/conversations/{job_id}` (status polling)
- **User-Scoped Recall:** `recall(query, user_id="alice")` filters memories by user, enabling per-user personalization
- **Token-Aware Chunking:** Long conversations automatically split into ~8K token chunks with 2-message overlap for context continuity
- **Cost Estimation:** CLI output includes estimated LLM cost after extraction
- **Partial Extraction Recovery:** Multi-chunk extraction preserves successful results even when individual chunks fail
- **Conversation Jobs Table:** New `conversation_jobs` migration for async job tracking (server mode)

### Changed

- `recall()` SDK method and MCP tool gain optional `user_id` parameter for memory scoping
- Extracted memories tagged with `source="conversation"`, `user_id`, `session_id`, `extraction_model`, and `extracted_at` metadata

## [0.7.0] — 2026-03-07 — "Living Archive"

### Added

- **On This Day (F1):** Query memories from the same month+day across all years, grouped by year. New MCP tool: `on_this_day`. New CLI command: `lore on-this-day`.
- **Verbatim Recall (F2):** Return the user's original words instead of AI summaries. New `--verbatim` flag on `recall` CLI command, `verbatim` parameter on MCP `recall` tool.
- **Temporal Filters (F3):** Date-range filtering on `recall` — supports `year`, `month`, `day`, `days_ago`, `hours_ago`, `before`, `after`, and window presets. New CLI flags and MCP parameters on `recall`.

## [0.6.0] — 2026-03-06 — "Open Brain"

### Added

- **Knowledge Graph (F1):** Entity and relationship extraction from memories with hop-by-hop graph traversal. New MCP tools: `graph_query`, `entity_map`, `related`.
- **Fact Extraction (F2):** Atomic (subject, predicate, object) fact extraction with automatic conflict detection and resolution (supersede, merge, contradict). New MCP tools: `extract_facts`, `list_facts`, `conflicts`.
- **Memory Consolidation (F3):** Auto-summarization of duplicate and related memory clusters. Deduplication, topic-based grouping, and LLM-powered summarization. New MCP tool: `consolidate`.
- **Memory Tiers (F4):** Working (1h TTL), short-term (7d TTL), and long-term (no expiry) memory tiers with tier-specific decay rates and recall weighting.
- **Importance Scoring (F5):** Adaptive importance decay based on tier, type, access frequency, and age. Importance-weighted recall scoring replaces the old additive model.
- **Metadata Enrichment (F6):** LLM-powered extraction of topics, entities, sentiment, and categories from memory content. New MCP tool: `enrich`.
- **Webhook Ingestion (F7):** REST-style ingestion endpoint with source adapters for Slack, Telegram, Git, and plain text. Content normalization and deduplication. New MCP tool: `ingest`.
- **Dialog Classification (F9):** Intent, domain, and emotion classification with rule-based fallback when no LLM is configured. New MCP tool: `classify`.
- **Prompt Export (F10):** Template-based memory export for LLM context injection. Supports XML (Claude), ChatML (OpenAI), markdown, and raw text formats with token budgeting. New MCP tool: `as_prompt`.
- 13 new MCP tools (7 → 20 total)
- Docker Compose setup for one-command deployment (Postgres + pgvector + Lore server)
- Setup guides for Claude Desktop, Cursor, VS Code, Windsurf, ChatGPT, Cline, and Claude Code
- Integration test suite covering 10 cross-feature scenarios
- Performance benchmark runner and documentation
- Quick start tutorial and comprehensive API reference
- Demo example scripts showing full cognitive pipeline

### Changed

- `recall()` now supports graph-enhanced retrieval via `graph_depth` parameter
- `remember()` now triggers enrichment, classification, and fact extraction pipelines when LLM is configured
- `list_memories()` supports filtering by tier
- `stats()` includes tier breakdown (`by_tier`) and importance statistics
- Memory scoring model changed from additive (`cosine * confidence * decay * votes`) to multiplicative (`cosine * time_adjusted_importance * tier_weight * graph_boost`)
- Memory data model extended with `tier`, `importance_score`, `access_count`, `last_accessed_at`, `archived`, `consolidated_into` fields

### Deprecated

- `decay_similarity_weight` and `decay_freshness_weight` constructor parameters — ignored, will be removed in v0.7.0

### Migration Notes

- New columns added to `memories` table (auto-migrated on startup for SQLite)
- 5 new tables: `facts`, `conflict_log`, `entities`, `relationships`, `entity_mentions`
- All LLM features are opt-in — existing installations work without changes
- See [migration guide](docs/migration-v0.5-to-v0.6.md) for full details

## [0.5.1]

- Importance scoring foundation, memory tier support.

## [0.3.0] — 2026-03-04

### Breaking Changes

- **Memory model replaces Lesson model.** The core data type is now `Memory` with a single `content` field instead of `problem`/`resolution`. Old aliases (`Lesson`, `QueryResult`, `LessonNotFoundError`) are preserved as deprecated exports.
- `stats()` now returns a `MemoryStats` dataclass instead of a plain dict.
- TypeScript SDK: `publish()` → `remember()`, `query()` → `recall()`, `list()` → `listMemories()`, `delete()` → `forget()`. Old methods are preserved as deprecated wrappers.

### New Features

- **`remember(content, ...)`** — universal memory storage with `type`, `context`, `tags`, `metadata`, `ttl`, `source`, `project`, and `confidence`.
- **`recall(query, ...)`** — semantic search with embedding-powered scoring.
- **`forget(id)`** — delete a memory by ID.
- **`context` field** on Memory — additional context stored alongside content.
- **TTL support** — memories can expire automatically via `ttl` (seconds) and `expires_at`.
- **Automatic TTL cleanup** — expired memories are cleaned up on `recall()` (debounced to 60s).
- **`MemoryStats` dataclass** — structured statistics with `total`, `by_type`, `oldest`, `newest`, `expired_cleaned`.
- **CLI enhancements:** `--context` flag on `remember`, `--tags` flag on `recall`, deprecated `publish`/`query` subcommands still work.
- **TypeScript SDK** fully migrated to Memory API with backward-compatible deprecated methods.

### Deprecated

- `publish()` — use `remember()` instead.
- `query()` — use `recall()` instead.
- `list()` — use `list_memories()` / `listMemories()` instead.
- `delete()` — use `forget()` instead.
- `Lesson` type alias — use `Memory` instead.
- `QueryResult` type alias — use `RecallResult` instead.
- `LessonNotFoundError` — use `MemoryNotFoundError` instead.
- `export_lessons()` / `import_lessons()` — deprecated, will be removed in 0.4.

## [0.2.1]

- SDK hardening: retry, graceful degradation, connection pooling, batching.

## [0.2.0]

- Initial public release with Lesson model, SQLite store, semantic search, PII redaction.
