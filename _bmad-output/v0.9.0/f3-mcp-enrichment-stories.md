# F3: MCP Remember Enrichment — User Stories

## Story 1: POST `/v1/memories` Triggers Async Enrichment
**As the** Lore server
**I want** new memories created via POST `/v1/memories` to trigger the enrichment pipeline
**So that** memories are automatically enriched with topics, entities, sentiment, and facts

### Acceptance Criteria
- [ ] When `LORE_ENRICHMENT_ENABLED=true`, enrichment runs after memory creation
- [ ] Enrichment is fire-and-forget — the POST response returns immediately with 201
- [ ] Enrichment updates the memory's `meta` JSONB with enrichment results
- [ ] If enrichment fails, the memory is still stored (no rollback)
- [ ] Enrichment only triggers when `enrich=true` in request (default when env var set)

## Story 2: MCP `remember` Tool Passes Enrichment Flag
**As an** MCP client
**I want** the `remember` tool to request enrichment by default when the server supports it
**So that** memories stored via MCP are automatically enriched

### Acceptance Criteria
- [ ] MCP remember tool works as before (enrichment handled by Lore SDK locally)
- [ ] Server-side POST `/v1/memories` enrichment is independent of MCP
