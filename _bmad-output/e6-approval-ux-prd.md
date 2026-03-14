# E6: Approval UX for Discovered Connections — PRD

**Epic:** E6 — Trust Layer
**Version Target:** v1.0.0
**Date:** 2026-03-14
**Status:** Draft

---

## 1. Problem Statement

Lore's knowledge graph auto-creates entities and relationships during enrichment, but users never see or validate these connections. Bad connections (false entity merges, incorrect relationships, spurious co-occurrences) degrade retrieval quality silently. Users have no way to:

- See what connections were automatically discovered
- Approve or reject those connections
- Prevent bad patterns from recurring

This erodes trust — users can't rely on a system that silently introduces errors into their knowledge base.

## 2. Goals

1. **Trust**: Give users visibility into auto-discovered connections and the power to approve/reject them
2. **Data Quality**: Remove bad connections before they pollute retrieval results
3. **Learning**: Track rejected patterns so the system doesn't re-suggest the same bad connections
4. **Backward Compatibility**: Existing users see zero workflow change — approval is opt-in
5. **Cross-Platform**: Works via CLI, Web UI, MCP tools, and REST API

## 3. Success Metrics

| Metric | Target |
|--------|--------|
| Existing tests still pass | 1761+ tests, 0 regressions |
| New test coverage | ≥30 new tests for E6 functionality |
| API response time for review endpoints | <200ms p95 |
| Backward compat | Default mode auto-approves all edges (no behavior change) |
| Rejected pattern dedup | Same connection not re-suggested after rejection |

## 4. User Stories

### US-1: View Pending Connections
**As a** user with approval mode enabled,
**I want to** see a list of pending knowledge graph connections,
**So that** I can decide which ones to keep and which to discard.

**Acceptance Criteria:**
- GET /v1/review returns pending relationships with entity names, types, and source memory
- CLI `lore review` shows pending items in a readable format
- MCP tool `review_digest` returns pending items grouped by topic
- Web UI shows a review queue section

### US-2: Approve a Connection
**As a** user reviewing pending connections,
**I want to** approve a connection I believe is correct,
**So that** it becomes a permanent part of my knowledge graph.

**Acceptance Criteria:**
- POST /v1/review/{id} with action=approve changes status from pending to approved
- Approved connections appear in graph queries and visualization
- CLI approve action works (single or batch)

### US-3: Reject a Connection
**As a** user reviewing pending connections,
**I want to** reject a connection I believe is incorrect,
**So that** it is removed from active graph queries and not re-suggested.

**Acceptance Criteria:**
- POST /v1/review/{id} with action=reject changes status to rejected
- Rejected connections are excluded from graph queries
- The rejection pattern (source_type, target_type, rel_type) is tracked

### US-4: Configure Approval Mode
**As a** user,
**I want to** opt-in to requiring approval for new connections,
**So that** I control my knowledge graph quality.

**Acceptance Criteria:**
- Config option `graph.approval` with values: `auto` (default), `required`
- Auto mode: new edges get status=approved (backward compat)
- Required mode: new edges get status=pending
- Can be toggled via `lore config set graph.approval required`

### US-5: Rejected Pattern Learning
**As a** user who has rejected connections,
**I want to** the system to remember what I rejected,
**So that** similar bad connections aren't re-suggested.

**Acceptance Criteria:**
- Rejected patterns stored in a `rejected_patterns` table
- Before creating a new relationship, check against rejected patterns
- Pattern matching on (source_entity_name, target_entity_name, rel_type)

### US-6: Review Digest via MCP
**As an** AI agent using Lore,
**I want to** get a digest of pending connections to present to the user,
**So that** I can mediate the review conversationally.

**Acceptance Criteria:**
- MCP tool `review_digest` returns pending items grouped by relationship type
- Includes entity names, types, source memory context
- Returns count of pending items
- Agent can then call approve/reject per item

### US-7: Web UI Review Queue
**As a** user browsing the Lore web UI,
**I want to** see and act on pending connections from the graph visualization,
**So that** I can review connections visually.

**Acceptance Criteria:**
- Review queue panel in the web UI sidebar
- Shows pending count badge
- Click to expand each pending connection with context
- Approve/reject buttons with immediate visual feedback
- Status filter on graph edges (show approved only, show pending, show all)

## 5. Edge Cases

1. **No pending items**: Review endpoints return empty list, CLI shows "Nothing to review"
2. **Approval mode toggled after data exists**: Existing approved edges stay approved; only new edges affected
3. **Duplicate rejection**: Rejecting same edge twice is idempotent
4. **Entity deleted after pending**: Pending relationships for deleted entities are auto-cleaned
5. **Concurrent approval**: Two users approving same edge — idempotent, last write wins on status
6. **Bulk operations**: Allow approving/rejecting all pending items at once
7. **Migration**: Existing relationships get status=approved during migration

## 6. Cross-Platform Considerations

| Platform | Integration Point | Notes |
|----------|------------------|-------|
| **OpenClaw** | MCP tool `review_digest` auto-surfaced via hooks | Agent presents pending connections conversationally |
| **Claude Code** | MCP tool + CLAUDE.md protocol | Add "check review_digest periodically" to instructions |
| **Codex** | MCP tools | Same tools available |
| **Cursor** | MCP tools + .cursorrules | Same tools available |
| **Web UI** | Review queue panel | Visual review with approve/reject buttons |
| **CLI** | `lore review` command | Interactive terminal review |

## 7. Out of Scope

- Auto-learning from approval patterns (e.g., "always approve co_occurs_with for tool entities") — future enhancement
- Notification system for pending items accumulating
- Per-project approval policies
- Approval workflows with multiple reviewers
