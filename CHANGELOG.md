# Changelog

All notable changes to Lore are documented here.

## [0.4.0] - 2026-03-03

### Added

- **Universal Memory API**: New `remember()`, `recall()`, `forget()`, `list_memories()`, `stats()` methods replace the old lesson-specific API
- **Memory types**: Support for note, lesson, snippet, fact, conversation, decision, and custom types
- **TTL / Expiration**: Memories can have a time-to-live (`ttl` parameter: `"7d"`, `"1h"`, `"30m"`)
- **CLI tool**: Full command-line interface (`lore remember`, `lore recall`, `lore forget`, `lore memories`, `lore stats`)
- **TypeScript SDK generalization**: TS SDK now supports `remember()`/`recall()`/`forget()`/`listMemories()`/`stats()` with remote mode
- **RemoteMemoryStore (TypeScript)**: HTTP client for `/v1/memories` endpoints
- **Setup guides**: Claude Desktop, Cursor, Windsurf, Docker, CLI, Python SDK, TypeScript SDK
- **Publishing guide**: Step-by-step instructions for PyPI and npm releases
- **Project scoping**: Memories can be scoped to projects (`LORE_PROJECT` env var or `project` parameter)
- **Background cleanup**: Expired memories are automatically excluded from queries
- **Multi-project QA**: End-to-end validation of project scoping across all tools

### Changed

- **Version**: Bumped to 0.4.0
- **PyPI metadata**: Updated classifiers (Beta status), added keywords (pgvector, semantic-search, rag)
- **TypeScript SDK**: Updated to 0.4.0, new description and keywords

### Backward Compatibility

- Legacy `publish()`/`query()` methods still work in both Python and TypeScript SDKs
- `Lesson`, `QueryResult`, and related types still exported
- Existing SQLite databases and PostgreSQL data continue to work

## [0.3.0] - 2026-02-16

### Added

- MCP server with 5 tools (remember, recall, forget, list, stats)
- REST API for memories (CRUD + search)
- Server-side embedding (clients don't need local models)
- `memories` table schema with pgvector support
- Data migration from legacy `lessons` table
- Multi-tenant support with API key auth
- SQLite local store for zero-config usage
- PII redaction pipeline (opt-in)
- Docker Compose setup (dev + production)

## [0.2.0] - 2026-02-13

### Added

- Initial Python SDK with Lesson API (publish/query)
- TypeScript SDK with SQLite and remote stores
- Embedding utilities (cosine similarity, time decay)
- Redaction pipeline for API keys, emails, IPs, credit cards
- MCP config examples for Claude Desktop, Cursor, Windsurf

## [0.1.0] - 2025-12-01

### Added

- Initial release
- SQLite-backed lesson store
- Basic embedding and search
