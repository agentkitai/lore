# Implementation Readiness Gate Check Report — Open Brain

**Reviewer:** PM / Scrum Master Gate Review
**Date:** 2026-03-03
**Artifacts Reviewed:** Product Brief, PRD, Architecture, Epics & Stories
**Verdict:** **PASS WITH CONCERNS**

---

## Executive Summary

The Open Brain planning artifacts are **substantially complete and well-aligned**. The product brief clearly articulates the pivot rationale, the PRD provides detailed functional requirements, the architecture is thorough and implementation-ready, and the stories are well-structured with clear acceptance criteria. The documents demonstrate strong internal consistency and a realistic understanding of the solo-developer constraints.

However, several issues must be addressed before development begins. Two are near-blocker level (FR count errors, missing stories for defined requirements), and several concerns should be tracked to prevent scope gaps during implementation.

**Overall quality: 8/10** — This is above-average planning for a solo-dev project. The honesty about risks and constraints is refreshing.

---

## 1. PRD Analysis

### Functional Requirements — Clarity & Testability

🟢 **OBSERVATION:** All 25 FRs have clear, testable acceptance criteria. Each FR includes priority, description, acceptance criteria with specific measurable outcomes, and dependency references. This is excellent for a solo-dev project — better than many team projects.

🟢 **OBSERVATION:** MCP tool descriptions (FR-001 through FR-005) include "WHEN TO USE" and "WHEN NOT TO USE" guidance, which is a smart product decision — the AI's usage behavior is shaped by these descriptions.

### Non-Functional Requirements — Specificity

🟢 **OBSERVATION:** NFRs are specific and measurable:
- NFR-001 has exact latency targets per operation (e.g., `remember` < 500ms, `recall` at 1K < 200ms)
- NFR-004 has concrete resource limits (< 512MB RAM, < 1 CPU core)
- NFR-005 explicitly states "No data telemetry / phone-home"

🟡 **CONCERN:** NFR-001 specifies "REST API concurrent requests: 50+" but doesn't define what "concurrent" means under load (sustained? burst?), or what the response time target is under concurrency. For V1 this is fine, but if cloud hosting (P3) materializes, this will need refinement.

### Priority Count Discrepancy

🔴 **BLOCKER:** PRD Appendix A states **"P0 Count: 12"** but the listed P0 FRs total **14**:
- FR-001 through FR-009 = **9** FRs
- FR-011 = **1** FR
- FR-014 through FR-017 = **4** FRs
- **Actual total: 14**, not 12

This also means the stated total of 23 FRs is incorrect — the actual total is **25** (FR-001 through FR-025). The math: 14 + 6 + 4 + 1 = 25.

**Fix required:** Update Appendix A counts to P0=14, Total=25. This matters because anyone planning sprint capacity from the summary table will underestimate scope.

### Missing Requirements from Product Brief

🟡 **CONCERN:** The product brief (§9, Technical Foundation) lists "Multi-org / API keys: ✅ Working" as an existing capability and says "None — works as-is." The PRD doesn't have a specific FR to validate/verify existing multi-org functionality works with the new `memories` table. It's implicitly covered (org_id is a column), but no explicit validation story or FR exists.

🟢 **OBSERVATION:** Product brief mentions "memory graph (relationships between memories)" and "memory agents (auto-summarize, auto-prune)" as Phase 4 items. These correctly do NOT appear in the PRD or stories — they're out of scope. Good scope discipline.

🟡 **CONCERN:** Product brief (Appendix B, Question 4) raises whether Lore's redaction feature should be kept, made optional, or removed, and recommends "keep as opt-in." The PRD codifies this as FR-023 (P2). However, the architecture document (§8.4) says redaction is disabled by default — this is consistent. But the **product brief marks it as an open question** while the PRD treats it as decided. The open question should be formally closed.

---

## 2. Requirements → Stories Traceability

### Traceability Matrix

| FR-ID | Priority | Description | Story ID(s) | Status |
|-------|----------|-------------|-------------|--------|
| FR-001 | P0 | `remember` tool | STORY-006 | ✅ Covered |
| FR-002 | P0 | `recall` tool | STORY-007 | ✅ Covered |
| FR-003 | P0 | `forget` tool | STORY-008 | ✅ Covered |
| FR-004 | P0 | `list` tool | STORY-008 | ✅ Covered |
| FR-005 | P0 | `stats` tool | STORY-009 | ✅ Covered |
| FR-006 | P0 | Memory schema | STORY-001, STORY-002 | ✅ Covered |
| FR-007 | P0 | Multi-project scoping | *(none explicit)* | ⚠️ **GAP** |
| FR-008 | P0 | Auto-embedding | STORY-005 | ✅ Covered |
| FR-009 | P0 | MCP server (stdio) | STORY-009 | ✅ Covered |
| FR-010 | P1 | SSE transport | STORY-023 | ✅ Covered |
| FR-011 | P0 | REST API endpoints | STORY-010, STORY-011, STORY-012 | ✅ Covered |
| FR-012 | P1 | Webhook ingestion | STORY-013 | ✅ Covered |
| FR-013 | P1 | CLI client | STORY-022 | ✅ Covered |
| FR-014 | P0 | Docker Compose | STORY-015 | ✅ Covered |
| FR-015 | P0 | Docker image registry | STORY-018 | ✅ Covered |
| FR-016 | P0 | README & quickstart | STORY-016 | ✅ Covered |
| FR-017 | P0 | MCP config snippets | STORY-017 | ✅ Covered |
| FR-018 | P1 | Python SDK | STORY-019, STORY-020 | ✅ Covered |
| FR-019 | P1 | TypeScript SDK | STORY-021 | ✅ Covered |
| FR-020 | P2 | Slack adapter | STORY-026 | ✅ Covered |
| FR-021 | P2 | Telegram adapter | STORY-027 | ✅ Covered |
| FR-022 | P3 | Web dashboard | STORY-029 | ✅ Covered |
| FR-023 | P2 | Redaction pipeline | STORY-028 | ✅ Covered |
| FR-024 | P1 | TTL / expiration | STORY-024 | ✅ Covered |
| FR-025 | P2 | Memory deduplication | *(none)* | ❌ **MISSING** |

### Coverage Gaps

🔴 **BLOCKER:** **FR-007 (Multi-Project Scoping, P0) has no dedicated story.** The PRD lists this as P0 with specific acceptance criteria:
- Project is free-text, not a separate table
- All five MCP tools respect project scoping
- Unscoped queries return all projects
- Project set per-tool or globally via env var

The Phase 0 delivery plan in the PRD even allocates "0.5 days" for it in Week 2. But no story exists for it. It's implicitly handled in STORY-004 (ServerStore) and STORY-006-009 (MCP tools), but there's no explicit story with acceptance criteria ensuring cross-cutting project scoping works end-to-end.

**Recommendation:** Either create STORY-XXX for FR-007, or explicitly add FR-007's acceptance criteria to STORY-004 and STORY-009 as sub-tasks with their own verification.

🟡 **CONCERN:** **FR-025 (Memory Deduplication, P2) has no story.** The story document doesn't mention deduplication at all. While this is P2 (not blocking V1), the backlog should include it for completeness.

**Recommendation:** Add a STORY-031 for FR-025 in Epic 7 or create a new "Memory Management" epic.

### Orphan Stories (no FR mapping)

🟢 **OBSERVATION:** STORY-025 (Setup guides + community Discord) doesn't map to a specific FR but supports FR-016/FR-017 and is a legitimate marketing/community task. Not a true orphan.

🟢 **OBSERVATION:** STORY-030 (Cloud hosting preparation) has no FR — this is intentional, as cloud hosting is explicitly a "pursue when demand justifies" item. Fine as a placeholder.

---

## 3. Architecture → Stories Alignment

### ADR Compliance

| ADR | Decision | Stories Aligned? | Notes |
|-----|----------|-----------------|-------|
| ADR-001 | MCP-first | ✅ | Epic 2 (MCP) is scheduled before Epic 3 (REST). Sprint 1 prioritizes MCP tools. |
| ADR-002 | Single table + metadata | ✅ | STORY-001 creates single `memories` table. |
| ADR-003 | Local embedding default | ✅ | STORY-005 implements ONNX MiniLM-L6-v2. |
| ADR-004 | Mono-repo pivot | ✅ | STORY-014 renames in-place. |
| ADR-005 | SSE as P1 | ✅ | STORY-023 is P1 in Sprint backlog. |
| ADR-006 | Server-side embedding | ✅ | STORY-005 + STORY-011 cover this. |
| ADR-007 | Dual API key prefix | ✅ | STORY-012 explicitly covers both prefixes. |
| ADR-008 | Simplified scoring | ✅ | Implicitly in STORY-004 (ServerStore search) and STORY-007 (recall). |

### SQLite Local Store Gap

🟡 **CONCERN:** The architecture defines two storage backends:
1. **ServerStore (asyncpg)** — covered by STORY-004
2. **SqliteStore (local mode)** — **no explicit story for updating to new schema**

STORY-006 mentions "Works in both local (SQLite) and remote (HTTP) modes" but there's no story specifically tasked with:
- Updating the SQLite store to use the new `memories` schema
- Implementing client-side cosine similarity search in SQLite mode
- Testing SQLite local mode end-to-end

This work is implied but not explicitly estimated or acceptance-criteria'd. For a solo dev, "implied" work that isn't tracked tends to be forgotten or underestimated.

**Recommendation:** Either create a dedicated story (STORY-XXX: "Update SqliteStore for new schema") or explicitly add it as sub-tasks within STORY-006 with size estimate.

### Migration Plan Coverage

🟢 **OBSERVATION:** The migration plan (Architecture §9) is thorough:
- Idempotent SQL migration
- Non-destructive (lessons table preserved)
- ON CONFLICT DO NOTHING for re-runnability
- Rollback plan documented
- Correctly mapped to STORY-001 and STORY-002

### Technical Dependencies

🟢 **OBSERVATION:** Dependencies between stories are correctly identified and the critical path analysis is accurate. The longest chain (STORY-001 → 003 → 006 → 007 → 008 → 009 → 017) at 7 stories deep is reasonable.

---

## 4. Epic Quality Review

### Acceptance Criteria Quality

🟢 **OBSERVATION:** Acceptance criteria across all 30 stories are specific and testable. Examples:
- STORY-001: "HNSW index created with m=16, ef_construction=64" — exact parameters
- STORY-005: "Embedding latency < 500ms per call" — measurable
- STORY-012: "Auth accepts `ob_sk_*` AND `lore_sk_*` prefixes" — verifiable

This is excellent. Every story can be definitively marked done/not-done.

### Story Sizing for Solo Developer

🟡 **CONCERN:** **Sprint 1 is overloaded.** 12 stories estimated at ~8 working days across 5 calendar days (1 week). The document acknowledges this is "aggressive" and justifies it by noting most work is "renaming/adapting existing code, not greenfield."

However:
- STORY-004 (ServerStore) is sized M (1-2 days) and involves extracting logic from route handlers into a new class + rewriting all SQL queries for the new schema. This is high-risk refactoring.
- STORY-006 through STORY-009 (all MCP tools) total 3.5 days of estimated effort. Combined with the ServerStore, that's 5-6 days on the critical path alone.
- Buffer for debugging/integration issues is minimal.

**Recommendation:** Consider splitting Sprint 1 into two: Week 1 = Schema + Core (STORY-001-005), Week 2 = MCP + REST (STORY-006-012). This reduces risk of a waterfall crunch.

🟢 **OBSERVATION:** Sprint 2 (5 stories, 4 working days) and Sprint 3 (6 stories, 8 days) are realistically scoped.

### Dependencies

🟢 **OBSERVATION:** Dependencies are correctly identified. STORY-005 (embedding) has no dependencies and can start day 1 — good parallelization opportunity noted.

🟡 **CONCERN:** STORY-014 (package rename) is described as "the scariest story — it touches every file." Yet it's sized as M (1-2 days). For a rename that touches every import in every Python file, plus pyproject.toml, Dockerfile, docker-compose, CI/CD — 2 days might be tight if any unexpected breakage occurs. The story notes wisely say "Do it early in Sprint 2 when you have focus, not Friday afternoon."

### Missing Stories for P0 Scope

🔴 **BLOCKER (already noted):** FR-007 (Multi-project scoping) needs a story or explicit sub-tasks.

🟡 **CONCERN:** No story covers **testing the end-to-end first-run experience** (PRD §6, Journey 1). The PRD describes a specific user journey:
1. `git clone && docker compose up -d`
2. Copy MCP config
3. Restart Claude Desktop
4. Say "Remember that my API runs on port 8080"
5. Next day, ask "What port does my API use?"
6. Get correct answer

There should be at least a verification task or acceptance test story that validates this complete flow. Individual stories test individual components, but nobody is tasked with testing the assembled product end-to-end.

**Recommendation:** Add a "STORY-XXX: End-to-end integration test + first-run validation" to Sprint 2.

### No "Update Memory" Capability

🟡 **CONCERN:** The schema includes an `updated_at` column, but **no MCP tool or REST endpoint allows updating an existing memory**. To modify a memory, users must `forget` + `remember` (delete and re-create). This is a deliberate simplification for V1, but:
- It's not explicitly called out as a non-goal or known limitation
- The `updated_at` column will never change (always equals `created_at`)
- Users may expect an "update" capability

**Recommendation:** Either add a note to the PRD explicitly stating "update is not supported in V1" or remove the `updated_at` column to avoid confusion.

---

## 5. Cross-Document Consistency

### Priority Alignment

| Aspect | Brief | PRD | Architecture | Stories |
|--------|-------|-----|-------------|---------|
| MCP tools | P0 | P0 | P0 (§12) | P0 (Epic 2) | ✅ |
| Schema migration | P0 | P0 | P0 (§12) | P0 (Epic 1) | ✅ |
| Docker deployment | P0 | P0 | P0 (§12) | P0 (Epic 4) | ✅ |
| README | P0 | P0 | P0 (§12) | P0 (Epic 4) | ✅ |
| SSE transport | P1 | P1 | P1 (ADR-005) | P1 (Epic 6) | ✅ |
| Webhook | P1 | P1 | P1 (§12) | P1 (Epic 3) | ✅ |
| Adapters | P2 | P2 | P2 (§12) | P2 (Epic 7) | ✅ |
| Dashboard | P3 | P3 | P3 (§12) | P3 (Epic 8) | ✅ |

🟢 **OBSERVATION:** Priorities are consistent across all four documents. Excellent alignment.

### Naming Conventions

🟢 **OBSERVATION:** Naming is consistent:
- Product name: "Open Brain" (two words, capitalized) — used everywhere as the product name
- Package/code name: "openbrain" (one word, lowercase) — used for imports, CLI, Docker, env vars
- API key prefix: "ob_sk_" — consistent across PRD, architecture, and stories
- No instances of mixing "Lore" as if it were the current product name (Lore is always referenced as the predecessor)

### Scope Boundaries

🟢 **OBSERVATION:** Non-goals/out-of-scope lists are consistent:
- PRD §2 (Non-Goals V1) aligns with product brief Phase 3-4 items
- Architecture §12 phases align with PRD §10 phases
- Stories correctly place items in the right epics based on priority

### Inconsistency: Trademark Check

🟡 **CONCERN:** PRD Appendix B labels the trademark/naming check as **"P0 blocker"** ("Don't invest in branding/content until name is clear"). But there is **no story for it**. The product brief (Appendix B) lists it as an open question. This is a non-technical task but is explicitly flagged as blocking in the PRD.

**Recommendation:** Add a non-technical task/story for trademark clearance, or at minimum document it as a pre-Sprint-1 prerequisite that Amit must complete manually.

---

## 6. Additional Findings

### Architecture Completeness

🟢 **OBSERVATION:** The architecture document is unusually thorough for a solo-dev project:
- 8 ADRs with clear rationale and consequences
- Full SQL schema with indexes
- MCP tool JSON schemas
- REST API endpoint table
- Docker Compose and Dockerfile
- Environment variable reference
- Project file structure

This is implementation-ready. A developer could start coding from this document without ambiguity.

### Time Pressure Risk

🟡 **CONCERN:** Multiple documents emphasize urgency: "Ship in 1-2 weeks," "the window is narrow," "if Amit doesn't ship in 2-4 weeks, someone else will." This urgency is real, but it creates pressure to cut corners on testing and quality. The planning documents should explicitly state what CAN be cut vs. what MUST ship.

**Recommendation:** Define a "minimum viable launch" subset — if Sprint 1 takes longer than expected, what's the absolute minimum that constitutes a launchable product? (Suggested: MCP server + Docker + README. REST API can come a day or two later.)

### Blog Post / Marketing

🟢 **OBSERVATION:** The PRD explicitly recommends "Write the launch blog post BEFORE finishing the code." The story plan doesn't include this as a formal story but Sprint 2 exit criteria mentions "Blog post drafted." This is good awareness but could be more formally tracked.

---

## 7. Findings Summary

### 🔴 BLOCKERS (2)

| # | Finding | Location | Fix |
|---|---------|----------|-----|
| B1 | PRD Appendix A P0 count says 12, actual is 14. Total says 23, actual is 25. | PRD §Appendix A | Update counts: P0=14, Total=25 |
| B2 | FR-007 (Multi-Project Scoping, P0) has no dedicated story or explicit sub-tasks in existing stories | Epics & Stories | Create story or add as explicit acceptance criteria to STORY-004 + STORY-009 |

### 🟡 CONCERNS (8)

| # | Finding | Location | Recommendation |
|---|---------|----------|---------------|
| C1 | FR-025 (Memory Deduplication, P2) has no story in the backlog | Epics & Stories | Add STORY-031 to backlog |
| C2 | SQLite local store update to new schema has no explicit story | Architecture §3.2 vs Stories | Add to STORY-006 or create dedicated story |
| C3 | Sprint 1 is overloaded (12 stories, ~8 days effort in 5 days) | Sprint Plan | Consider splitting into 2 sub-sprints |
| C4 | No end-to-end first-run integration test story | Stories / PRD §6 | Add integration test story to Sprint 2 |
| C5 | Trademark check is labeled "P0 blocker" in PRD but has no story | PRD Appendix B | Add as pre-Sprint prerequisite task |
| C6 | `updated_at` column exists but no update operation defined | PRD §4 / Architecture §4 | Document as "not in V1" or remove column |
| C7 | NFR-001 concurrent request target (50+) lacks definition of conditions | PRD §5 | Acceptable for V1; refine if cloud pursued |
| C8 | Product brief has open questions (redaction, embedding model) that PRD treats as decided | Brief Appendix B vs PRD | Formally close open questions in brief |

### 🟢 OBSERVATIONS (8)

| # | Finding |
|---|---------|
| O1 | All FRs have clear, testable acceptance criteria — excellent quality |
| O2 | MCP tool descriptions include usage guidance for AI clients — smart product decision |
| O3 | NFRs are specific with measurable targets |
| O4 | Architecture ADRs are well-documented with rationale and consequences |
| O5 | Priorities align across all four documents |
| O6 | Naming conventions are consistent throughout |
| O7 | Migration plan is thorough, idempotent, and non-destructive |
| O8 | Architecture document is implementation-ready — no ambiguity |

---

## 8. Final Verdict

### **PASS WITH CONCERNS**

The planning artifacts are **ready for implementation** with the following conditions:

1. **Before Sprint 1 starts:**
   - Fix PRD Appendix A counts (B1) — 5-minute fix
   - Add FR-007 coverage to stories (B2) — either a new story or explicit sub-tasks
   - Confirm trademark status for "Open Brain" (C5) — or document decision to proceed at risk

2. **During Sprint 1:**
   - Track SQLite store update explicitly (C2)
   - Monitor Sprint 1 velocity; be prepared to extend to 8-9 days instead of 5 (C3)

3. **Before launch:**
   - Add end-to-end integration validation (C4)
   - Close product brief open questions formally (C8)

4. **Backlog hygiene:**
   - Add FR-025 (dedup) story (C1)
   - Decide on `updated_at` column (C6)

**Bottom line:** These are thorough, honest, well-aligned planning documents. The identified gaps are fixable in hours, not days. The product vision is clear, the architecture is sound, and the stories are implementable. Ship it. 🚀

---

*Report generated 2026-03-03. Review all blockers before committing to Sprint 1.*
