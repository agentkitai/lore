# Lore v0.9.0 Sprint Brief — "Clean Slate"

## Vision
Complete the transformation from "lessons learned tool" to "universal AI memory platform" by:
1. Migrating the legacy schema (lessons → memories, problem/resolution → content/context)
2. Building the `lore setup` CLI for one-command runtime integration
3. Ensuring all memory paths trigger the full LLM enrichment pipeline

## Features

### F1: Schema Migration (lessons → memories)
**Priority:** Critical
**Scope:** Database migration, API routes, server code, SDK, tests
- Rename DB table `lessons` → `memories`
- Rename columns `problem` → `content`, `resolution` → `context`  
- Add new API routes `/v1/memories` (CRUD)
- Keep `/v1/lessons` as deprecated aliases (backward compat)
- Update retrieve endpoint to use `content` directly (remove SQL hack)
- Update all server code references
- Migration script for existing installations
- Update TypeScript SDK

### F2: Setup CLI (`lore setup <runtime>`)
**Priority:** High
**Scope:** New CLI command, bundled hook scripts, settings.json manipulation
- `lore setup claude-code` — creates hook + configures settings.json
- `lore setup openclaw` — creates hook + enables it
- `lore setup --status` — show what's configured
- `lore setup --remove <runtime>` — uninstall hooks
- Hook scripts bundled inside lore-sdk package
- Auto-detect Lore server URL and test connection
- Support custom server URLs

### F3: MCP Remember Enrichment
**Priority:** High  
**Scope:** Server route change, MCP handler update
- `POST /v1/memories` triggers async enrichment pipeline when LORE_ENRICHMENT_ENABLED=true
- Same pipeline as conversation ingest: enrich → classify → extract facts → knowledge graph
- MCP `remember` tool passes `enrich=true` by default
- Fire-and-forget: don't block the response waiting for enrichment

## Non-Goals
- No new MCP tools
- No new LLM features
- No Docker/deployment changes
- No breaking API changes (deprecated aliases maintain backward compat)

## Technical Notes
- Migration must handle existing data (rehash/rewrite problem→content, resolution→context)
- The retrieve endpoint currently does `COALESCE(problem,'') || resolution AS content` — after migration this becomes just `content`
- Tests: update all fixtures referencing `lessons` table/columns
- CI must pass on all 3 Python versions + TypeScript + PostgreSQL
