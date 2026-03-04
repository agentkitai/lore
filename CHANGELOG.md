# Changelog

## 0.3.0 — 2026-03-04

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

## 0.2.1

- SDK hardening: retry, graceful degradation, connection pooling, batching.

## 0.2.0

- Initial public release with Lesson model, SQLite store, semantic search, PII redaction.
