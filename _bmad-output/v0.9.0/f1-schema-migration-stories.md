# F1: Schema Migration — User Stories

## Story 1: Database Migration Script
**As a** Lore server operator
**I want** the `lessons` table renamed to `memories` with columns `problem` → `content` and `resolution` → `context`
**So that** the schema reflects the universal memory platform identity

### Acceptance Criteria
- [ ] Migration `009_rename_to_memories.sql` renames table and columns idempotently
- [ ] Existing data is preserved after migration
- [ ] All indexes are updated to reference new table/column names
- [ ] Migration is safe to run multiple times (IF NOT EXISTS / DO $$ guards)

## Story 2: New `/v1/memories` API Routes
**As an** API consumer
**I want** CRUD endpoints at `/v1/memories` using `content` and `context` fields
**So that** I can interact with the memory API using the new naming convention

### Acceptance Criteria
- [ ] POST `/v1/memories` creates a memory with `content` (required) and `context` (optional)
- [ ] GET `/v1/memories/{id}` returns a memory
- [ ] PATCH `/v1/memories/{id}` updates a memory
- [ ] DELETE `/v1/memories/{id}` deletes a memory
- [ ] GET `/v1/memories` lists memories with pagination
- [ ] POST `/v1/memories/search` performs semantic search
- [ ] POST `/v1/memories/{id}/access` tracks access

## Story 3: Deprecated `/v1/lessons` Aliases
**As an** existing API consumer
**I want** `/v1/lessons` endpoints to continue working
**So that** my integrations don't break during the transition

### Acceptance Criteria
- [ ] All existing `/v1/lessons` routes still function
- [ ] Lesson routes use the same `memories` table under the hood
- [ ] Response models still use `problem`/`resolution` field names for backward compat

## Story 4: Retrieve Endpoint Cleanup
**As a** developer
**I want** the retrieve endpoint to read `content` directly
**So that** the COALESCE SQL hack is removed

### Acceptance Criteria
- [ ] `GET /v1/retrieve` queries `content` column directly
- [ ] No more `COALESCE(problem, '') || ...` SQL construction
- [ ] Results are identical in format and scoring

## Story 5: Conversation Extraction Uses New Schema
**As the** conversation extraction pipeline
**I want** extracted memories persisted using `content`/`context` columns
**So that** the pipeline is consistent with the new schema

### Acceptance Criteria
- [ ] `_process_job()` inserts into `memories` table using `content`/`context` columns
- [ ] Existing conversation extraction tests pass
