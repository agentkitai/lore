# E6: Approval UX — Implementation Stories

**Sprint:** E6 Trust Layer
**Date:** 2026-03-14

---

## Batch 1: Schema & Store Layer (Foundation)

### Story 1.1: Database Migration — Add status column + rejected_patterns table
**Priority:** P0 (blocker for all other stories)

**Acceptance Criteria:**
- [ ] Migration `011_approval_ux.sql` creates status column on relationships with default 'approved'
- [ ] Creates `rejected_patterns` table with unique index
- [ ] Migration is idempotent (uses IF NOT EXISTS / IF NOT EXISTS patterns)
- [ ] Existing relationships get status='approved' automatically via column default

**Implementation Hints:**
- Follow pattern of `007_knowledge_graph.sql` for table creation
- Use `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for idempotency
- Add index on status column for filtering performance

**Tests:**
- Migration file syntax valid (executed during test setup)

---

### Story 1.2: Types — Add status to Relationship + RejectedPattern dataclass
**Priority:** P0

**Acceptance Criteria:**
- [ ] `Relationship` dataclass gains `status: str = "approved"` field
- [ ] New `RejectedPattern` dataclass with id, source_name, target_name, rel_type, rejected_at, reason
- [ ] New `VALID_REVIEW_STATUSES` tuple: ("pending", "approved", "rejected")
- [ ] New `ReviewItem` dataclass for API responses

**Tests:**
- Relationship default status is "approved"
- RejectedPattern instantiation

---

### Story 1.3: Store Layer — Add review methods to base + memory store
**Priority:** P0

**Acceptance Criteria:**
- [ ] `Store` base class gets new methods with default no-op implementations:
  - `list_pending_relationships(limit) -> List[Relationship]`
  - `update_relationship_status(rel_id, status) -> bool`
  - `save_rejected_pattern(source_name, target_name, rel_type, ...) -> None`
  - `is_rejected_pattern(source_name, target_name, rel_type) -> bool`
  - `list_rejected_patterns(limit) -> List[dict]`
- [ ] `MemoryStore` implements all methods with in-memory storage
- [ ] Tests for MemoryStore review methods

**Tests:**
- list_pending_relationships returns only pending
- update_relationship_status changes status
- save_rejected_pattern + is_rejected_pattern round-trip
- Duplicate rejection is idempotent

---

## Batch 2: API + CLI + MCP

### Story 2.1: REST API — Review endpoints
**Priority:** P1

**Acceptance Criteria:**
- [ ] `GET /v1/review` returns pending relationships with entity context
- [ ] `POST /v1/review/{id}` approves or rejects with side effects
- [ ] `POST /v1/review/bulk` handles batch operations
- [ ] Proper error handling (404 for unknown ID, 400 for invalid action)
- [ ] Router registered in app.py

**Implementation Hints:**
- New file: `src/lore/server/routes/review.py`
- Follow pattern of `graph.py` — use `get_pool()`, Pydantic models
- On reject: also insert into rejected_patterns
- Register in `app.py` with `app.include_router(review_router)`

**Tests:**
- GET /v1/review returns pending items
- POST approve changes status
- POST reject changes status + creates rejected pattern
- Bulk approve/reject
- 404 for non-existent relationship
- Empty pending list returns empty array

---

### Story 2.2: CLI — `lore review` command
**Priority:** P1

**Acceptance Criteria:**
- [ ] `lore review` lists pending connections with entity names
- [ ] `lore review --approve <id>` approves a relationship
- [ ] `lore review --reject <id>` rejects a relationship
- [ ] `lore review --approve-all` approves all pending
- [ ] `lore review --reject-all` rejects all pending
- [ ] Shows "Nothing to review." when no pending items

**Implementation Hints:**
- Add `review` subparser in `build_parser()`
- New `cmd_review(args)` function
- Uses Lore SDK methods (which delegate to store)

**Tests:**
- CLI review with no pending items
- CLI review lists items
- CLI review --approve works
- CLI review --reject works

---

### Story 2.3: MCP Tool — `review_digest`
**Priority:** P1

**Acceptance Criteria:**
- [ ] New MCP tool `review_digest` returns pending connections
- [ ] Grouped by relationship type for conversational presentation
- [ ] Includes entity names, types, and source memory context
- [ ] Returns total pending count

**Implementation Hints:**
- Add to `src/lore/mcp/server.py`
- Follow existing tool pattern with `@mcp.tool(description=...)`

**Tests:**
- review_digest with no pending returns empty
- review_digest returns correctly structured data
- review_digest respects limit parameter

---

### Story 2.4: Lore SDK — Add review methods
**Priority:** P1

**Acceptance Criteria:**
- [ ] `Lore.get_pending_reviews(limit)` returns pending relationships with entity info
- [ ] `Lore.review_connection(rel_id, action, reason)` approves or rejects
- [ ] `Lore.review_all(action)` bulk approves or rejects
- [ ] On reject: saves rejected pattern
- [ ] All methods delegate to store

**Tests:**
- get_pending_reviews returns only pending
- review_connection approve
- review_connection reject + pattern saved
- review_all bulk

---

## Batch 3: Graph Query Filtering + Rejected Patterns

### Story 3.1: Filter graph queries by status
**Priority:** P1

**Acceptance Criteria:**
- [ ] `GET /v1/ui/graph` only returns approved relationships (edges)
- [ ] `GET /v1/ui/entity/{id}` only shows approved connections
- [ ] `GET /v1/ui/topics/{name}` only shows approved relationships
- [ ] Graph traverser only traverses approved edges
- [ ] Export includes all relationships with status field

**Implementation Hints:**
- Add `WHERE status = 'approved'` (or `WHERE status != 'rejected'`) to relationship queries
- For MemoryStore: filter in list/query methods

**Tests:**
- Graph endpoint excludes rejected edges
- Graph endpoint excludes pending edges (only approved shown)
- Entity detail only shows approved connections

---

### Story 3.2: Rejected pattern checking on relationship creation
**Priority:** P1

**Acceptance Criteria:**
- [ ] Before creating a new relationship, check `is_rejected_pattern()`
- [ ] If pattern matches, skip relationship creation silently
- [ ] Config `graph.approval` controls initial status of new relationships
- [ ] Config value readable via `lore config get graph.approval`

**Implementation Hints:**
- Modify relationship creation in MemoryStore.save_relationship
- Check config for approval mode
- Add `LORE_GRAPH_APPROVAL` env var support

**Tests:**
- Rejected pattern blocks new relationship creation
- Config auto mode: new relationships get approved
- Config required mode: new relationships get pending

---

## Batch 4: Web UI

### Story 4.1: Web UI — Review queue panel
**Priority:** P2

**Acceptance Criteria:**
- [ ] New review panel in sidebar
- [ ] Shows pending count badge
- [ ] Lists pending connections with approve/reject buttons
- [ ] API calls to approve/reject update UI immediately
- [ ] Pending edges shown as dashed lines in graph

**Implementation Hints:**
- New file: `src/lore/ui/src/panels/review.js`
- Add API methods to `api.js`
- Wire into `index.js` panel system
- Update `renderer.js` for dashed pending edges

**Tests:**
- Panel renders (manual/visual — no automated UI tests needed)

---

## Story Dependency Graph

```
1.1 (migration) ──┐
1.2 (types)    ────┤
1.3 (store)    ────┼──> 2.1 (API) ──────> 3.1 (graph filter)
                   │    2.2 (CLI) ──────> 3.2 (rejected patterns)
                   │    2.3 (MCP)
                   │    2.4 (SDK)
                   └──────────────────────> 4.1 (Web UI)
```

## Estimated Effort

| Batch | Stories | Est. Time |
|-------|---------|-----------|
| Batch 1 | 1.1, 1.2, 1.3 | 15 min |
| Batch 2 | 2.1, 2.2, 2.3, 2.4 | 20 min |
| Batch 3 | 3.1, 3.2 | 10 min |
| Batch 4 | 4.1 | 10 min |
| **Total** | **10 stories** | **~55 min** |
