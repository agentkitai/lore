# E6: Approval UX — Architecture Document

**Date:** 2026-03-14
**Epic:** E6 — Trust Layer

---

## 1. Database Schema Changes

### 1.1 Migration: `011_approval_ux.sql`

Add `status` column to `relationships` table and create `rejected_patterns` table.

```sql
-- Add status column to relationships (default 'approved' for backward compat)
ALTER TABLE relationships ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'approved';
CREATE INDEX IF NOT EXISTS idx_rel_status ON relationships(status);

-- Rejected patterns table — tracks what not to re-suggest
CREATE TABLE IF NOT EXISTS rejected_patterns (
    id              TEXT PRIMARY KEY,
    source_name     TEXT NOT NULL,
    target_name     TEXT NOT NULL,
    rel_type        TEXT NOT NULL,
    rejected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_memory_id TEXT,
    reason          TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rp_unique
    ON rejected_patterns(source_name, target_name, rel_type);
```

**Key decisions:**
- Default `status='approved'` ensures all existing rows are backward compatible without a data migration
- `rejected_patterns` uses entity names (not IDs) because entity IDs can change during merges, but patterns are about the *concept* pairing
- Unique index on rejected_patterns prevents duplicate rejections

### 1.2 Affected Queries

All graph queries that currently read `relationships` without filtering must add:
```sql
WHERE status = 'approved'  -- or: WHERE status != 'rejected'
```

This affects:
- `GET /v1/ui/graph` — edge loading
- `GET /v1/ui/entity/{id}` — connected entities
- `GET /v1/ui/topics/{name}` — related entities
- Graph traverser queries
- Export queries (should export all statuses with status field)

## 2. API Endpoints

### 2.1 GET /v1/review

Returns pending relationships with full entity context.

**Query params:**
- `limit` (int, default 50)
- `rel_type` (optional filter)

**Response:**
```json
{
  "pending": [
    {
      "id": "rel-id",
      "source_entity": {"id": "...", "name": "Python", "entity_type": "language"},
      "target_entity": {"id": "...", "name": "FastAPI", "entity_type": "framework"},
      "rel_type": "uses",
      "weight": 1.0,
      "source_memory_id": "mem-id",
      "source_memory_content": "We use FastAPI...",
      "created_at": "2026-03-14T..."
    }
  ],
  "total_pending": 5
}
```

### 2.2 POST /v1/review/{relationship_id}

Approve or reject a relationship.

**Request body:**
```json
{
  "action": "approve" | "reject",
  "reason": "optional reason for rejection"
}
```

**Response:**
```json
{
  "id": "rel-id",
  "status": "approved",
  "previous_status": "pending"
}
```

**Side effects on reject:**
- Set relationship status = 'rejected'
- Insert into `rejected_patterns` table

### 2.3 POST /v1/review/bulk

Bulk approve/reject multiple relationships.

**Request body:**
```json
{
  "action": "approve" | "reject",
  "ids": ["rel-1", "rel-2"],
  "reason": "optional"
}
```

## 3. CLI Command

### `lore review`

Non-interactive listing of pending connections (since tests can't use interactive prompts).

```
$ lore review
Pending connections (3 total):

  1. Python --[uses]--> FastAPI
     Source: "We use FastAPI for the API layer"
     Created: 2026-03-14

  2. Lore --[depends_on]--> PostgreSQL
     Source: "Lore stores data in PostgreSQL"
     Created: 2026-03-14

Use --approve <id> or --reject <id> to act on items.
Use --approve-all or --reject-all for bulk actions.
```

**Flags:**
- `--approve <id>` — approve a specific relationship
- `--reject <id>` — reject a specific relationship
- `--approve-all` — approve all pending
- `--reject-all` — reject all pending
- `--limit <n>` — limit results (default 50)

## 4. MCP Tool

### `review_digest`

```python
@mcp.tool(description="Get pending knowledge graph connections for review...")
async def review_digest(limit: int = 20) -> dict:
    """Returns pending connections grouped by relationship type."""
```

Returns a structured digest suitable for agent-mediated conversational review.

## 5. Store Layer Changes

### 5.1 Base Store (new methods)

```python
def list_pending_relationships(self, limit: int = 50) -> List[Relationship]: ...
def update_relationship_status(self, rel_id: str, status: str) -> bool: ...
def save_rejected_pattern(self, source_name: str, target_name: str, rel_type: str, ...) -> None: ...
def is_rejected_pattern(self, source_name: str, target_name: str, rel_type: str) -> bool: ...
def list_rejected_patterns(self, limit: int = 100) -> List[dict]: ...
```

### 5.2 MemoryStore (in-memory implementation)

Add `_rejected_patterns: List[dict]` and implement all new methods.

### 5.3 HttpStore

Delegate to REST API endpoints.

## 6. Relationship Creation Changes

In `graph/relationships.py` (or wherever relationships are created), before inserting:

1. Check `is_rejected_pattern()` — if pattern is rejected, skip creation
2. Check config `graph.approval` — if `required`, set `status='pending'`; if `auto`, set `status='approved'`

## 7. Web UI Changes

### 7.1 Review Queue Panel

New panel in the sidebar (alongside topics, filters, stats):
- Badge with pending count
- List of pending connections with approve/reject buttons
- Clicking an item highlights the edge on the graph

### 7.2 Edge Status Visualization

- Approved edges: solid lines (current behavior)
- Pending edges: dashed lines, lower opacity
- Rejected edges: hidden by default (can show via filter)

### 7.3 API Client Updates

Add to `api.js`:
```javascript
async getReviewQueue(limit = 50) { ... }
async reviewRelationship(id, action, reason) { ... }
async reviewBulk(action, ids) { ... }
```

## 8. Backward Compatibility

| Scenario | Behavior |
|----------|----------|
| Fresh install | All new relationships auto-approved (default config) |
| Existing data + migration | All existing relationships get status='approved' (ALTER TABLE default) |
| User enables approval mode | Only new relationships get status='pending' |
| User disables approval mode | New relationships auto-approved again; pending items stay pending |
| Graph queries | Only return approved relationships (filter added) |
| Export | Include status field; import respects it |

## 9. Config Changes

New config key: `graph.approval`
- Values: `auto` (default), `required`
- Set via: `lore config set graph.approval required`
- Read via: `lore config get graph.approval`
- Environment variable: `LORE_GRAPH_APPROVAL`

## 10. File Changes Summary

| File | Change |
|------|--------|
| `migrations/011_approval_ux.sql` | New migration |
| `src/lore/types.py` | Add status to Relationship, add RejectedPattern type |
| `src/lore/store/base.py` | Add new abstract methods |
| `src/lore/store/memory.py` | Implement new methods |
| `src/lore/store/http.py` | Delegate to API |
| `src/lore/server/routes/review.py` | New route file |
| `src/lore/server/routes/graph.py` | Add status filter to queries |
| `src/lore/server/app.py` | Register review router |
| `src/lore/cli.py` | Add `review` subcommand |
| `src/lore/mcp/server.py` | Add `review_digest` tool |
| `src/lore/lore.py` | Add review methods |
| `src/lore/ui/src/panels/review.js` | New UI panel |
| `src/lore/ui/src/api.js` | Add review API methods |
| `src/lore/ui/src/index.js` | Wire up review panel |
| `tests/test_review.py` | New test file |
