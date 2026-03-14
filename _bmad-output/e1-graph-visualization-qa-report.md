# E1: Graph Visualization — QA Report

**Date:** March 14, 2026
**QA Engineer:** Claude (BMAD)
**Epic:** E1 — Graph Visualization (Web UI)
**Target Release:** v0.11.0

---

## Overall Verdict: PASS

All three phases (MVP, Interactivity, Polish) are implemented with working backend APIs, CLI integration, and a complete frontend. 52 tests pass covering all endpoints. One bug was found and fixed during QA (MemoryStore missing `list_all_entity_mentions` override).

---

## Test Results

```
52 passed in 0.49s
```

- **Original tests:** 41 (all passing)
- **Tests added during QA:** 11
- **Total:** 52

---

## Story-by-Story Verification

### Phase 1: Foundation (MVP)

| Story | Title | Status | Notes |
|-------|-------|--------|-------|
| S1 | Store Interface Extensions | **PASS** | `list_entities()`, `list_relationships()`, `list_all_entity_mentions()` implemented in base, SQLite, and MemoryStore. **Bug fixed during QA:** `MemoryStore.list_all_entity_mentions()` was missing — added override. |
| S2 | Frontend Build Pipeline | **PASS** | `package.json`, `build.mjs`, D3 dependencies installed. `dist/` contains `index.html` (13.6KB) + `app.js` (99.7KB). Total bundle: 113KB — well under 500KB limit. |
| S3 | Color Palette + Utilities | **PASS** | `colors.js` matches all 9 memory type colors and 10 entity type colors from PRD Section 5. `getNodeColor()` with fallback. `utils.js` has `debounce`, `truncateText`, `formatDate`. |
| S4 | Backend Graph API (`/v1/ui/graph`) | **PASS** | Returns `{nodes, edges, stats}`. Supports all query params: project, type, tier, min_importance, since, until, limit, include_orphans. Memory content NOT included (label only). 9 tests covering empty DB, filters, limits, orphan exclusion, edge endpoint validation. |
| S5 | CLI Command (`lore ui`) + UI App Factory | **PASS** | `cmd_ui()` in cli.py starts FastAPI via uvicorn. `create_ui_app()` factory in `ui_app.py`. Supports `--port`, `--host`, `--no-open`. Security warning on `0.0.0.0`. 7 CLI tests pass. |
| S6 | D3 Force Simulation + Canvas Renderer | **PASS** | `simulation.js` configures D3-force with forceLink, forceManyBody, forceCenter, forceCollide. `renderer.js` draws circles (memories) and hexagons (entities) on Canvas. Node sizing by importance/mention_count. Colors from palette. Filtered nodes at 10% opacity. Labels at zoom > 0.8. |
| S7 | Zoom, Pan, Drag Interactions | **PASS** | `interaction.js` uses D3-zoom (scale 0.1–8x). Quadtree for hit testing. Click/drag/pan. `centerOnNode()` with smooth transition. Cursor changes. |
| S8 | Detail Panel (Memory + Entity Views) | **PASS** | `/v1/ui/memory/{id}` and `/v1/ui/entity/{id}` endpoints return full details + connections. 404 for unknown IDs. Panel slides in/out. LRU cache (200 entries) in `api.js`. Escape key closes. |

### Phase 2: Interactivity

| Story | Title | Status | Notes |
|-------|-------|--------|-------|
| S9 | Filter Sidebar | **PASS** | `filters.js` — project dropdown, type/entity type/tier checkboxes, importance slider, date range pickers. Debounced. Filter badge shows active count. Reset button. Collapsible sidebar. |
| S10 | Search API (`/v1/ui/search`) | **PASS** | POST endpoint with keyword search. Case-insensitive. Returns results with scores, query_time_ms. Supports filters. Returns 400 on unknown mode. 8 tests. |
| S11 | Hover Tooltips + Edge Labels | **PASS** | `interaction.js` handles mousemove for node/edge hover. Tooltip div positioned near mouse. Edge hover detection with perpendicular distance. |
| S12 | Search Bar UI + Result Highlighting | **PASS** | `search.js` — debounced input (300ms), results dropdown, pulsing glow for matches, dim non-matches. Click result centers on node. Escape clears. |
| S13 | URL State Sync | **PASS** | `state.js` `_syncUrlState()` uses `history.replaceState()`. `restoreFromUrl()` reads params on load. Supports project, type, tier, importance, dates, search, selected node in hash. |
| S14 | Right-Click Context Menu | **PASS** | `interaction.js` handles contextmenu event. Menu items: Copy ID, Copy Recall Command, View Details. Clipboard API. Dismiss on click-away/Escape. Long-press for touch (500ms). |

### Phase 3: Polish

| Story | Title | Status | Notes |
|-------|-------|--------|-------|
| S15 | Cluster/Stats/Timeline APIs | **PASS** | `/v1/ui/graph/clusters` (group_by project/type), `/v1/ui/stats` (totals, breakdowns, top entities, recent counts), `/v1/ui/timeline` (hour/day/week/month buckets). All handle empty DB. Project filter. 8+ tests. |
| S16 | Cluster View (Frontend) | **PASS** | `layout.js` — toggle buttons in header (Force/By Project/By Type). Fetches cluster API. Convex hull drawing. Cluster labels. Smooth transition. |
| S17 | Timeline Scrubber | **PASS** | `timeline.js` — bottom bar with mini bar chart. Draggable range handles. Collapsible. Hidden on mobile. |
| S18 | Stats Dashboard Panel | **PASS** | `stats.js` — total cards, donut chart (by type), bar chart (by project), avg importance, top 5 entities (clickable), recent activity. Re-fetches on filter change. |
| S19 | Minimap | **PASS** | `minimap.js` — 150x150px canvas. All nodes as 2px dots. Viewport rectangle. Click to pan. Hidden on mobile. |
| S20 | Responsive Layout + Touch | **PASS** | CSS media queries in `index.html`: tablet (1024px) collapses sidebar, mobile (768px) uses bottom sheets, hides timeline/minimap. Debounced resize handler. Touch support via D3-zoom. |
| S21 | Keyboard Navigation + A11y | **PASS** | Tab/Shift+Tab cycles nodes by importance. Enter opens detail. Escape closes panels. Arrow keys pan. +/- zoom. ARIA live region for selections. High contrast mode toggle. `.sr-only` class. |

---

## Issues Found

### Bug Fixed During QA

| # | Severity | Description | Status |
|---|----------|-------------|--------|
| 1 | **Medium** | `MemoryStore.list_all_entity_mentions()` not overridden — returns `[]` from base class default. This means mention edges (memory↔entity) would be missing when using MemoryStore (tests, dev mode). | **FIXED** — Added override in `src/lore/store/memory.py` |

### No Other Issues Found

The implementation is thorough and matches the PRD/architecture specs.

---

## Additional Tests Written

11 new tests added to `tests/server/test_ui_routes.py`:

| Class | Test | What it verifies |
|-------|------|-----------------|
| `TestGraphMentionEdges` | `test_mention_edges_present` | 2 mention edges appear in graph response |
| `TestGraphMentionEdges` | `test_mention_edge_sources_are_memories` | Mention edge sources are memory node IDs |
| `TestGraphMentionEdges` | `test_entity_entity_edges_present` | Entity↔entity relationship edges present |
| `TestGraphMentionEdges` | `test_total_edge_count` | Total edge count = mentions + relationships |
| `TestGraphNodeStructure` | `test_memory_node_fields` | Memory nodes have all PRD-specified fields |
| `TestGraphNodeStructure` | `test_entity_node_fields` | Entity nodes have all PRD-specified fields |
| `TestSearchEdgeCases` | `test_search_case_insensitive` | Search is case-insensitive |
| `TestSearchEdgeCases` | `test_search_entity_by_alias` | Entities found by alias match |
| `TestTimelineBuckets` | `test_hourly_buckets` | Hourly bucket format correct |
| `TestTimelineBuckets` | `test_monthly_buckets` | Monthly bucket format correct |
| `TestMemoryDetailConnections` | `test_connected_memories_via_shared_entity` | Connected memories via shared entity |

---

## Non-Functional Requirements Check

| Requirement | Target | Actual | Status |
|-------------|--------|--------|--------|
| Bundle size (HTML+JS) | < 500KB | 113KB | **PASS** |
| No external CDN deps | Everything bundled | All in dist/ | **PASS** |
| Served on localhost only | 127.0.0.1 default | Default host is 127.0.0.1 | **PASS** |
| Security warning on 0.0.0.0 | Warn user | Prints warning to stderr | **PASS** |
| API response format | JSON with nodes/edges/stats | Matches PRD spec | **PASS** |
| Dark theme | PRD Section 5 colors | CSS variables match | **PASS** |
| Responsive breakpoints | Desktop/Tablet/Mobile | 3 breakpoints in CSS | **PASS** |
| Accessibility | ARIA, keyboard nav, high contrast | All implemented | **PASS** |

---

## File Inventory

### Backend (Python)
- `src/lore/server/routes/ui.py` — All 7 API endpoints (graph, memory detail, entity detail, search, clusters, stats, timeline)
- `src/lore/server/ui_app.py` — FastAPI app factory with static file serving
- `src/lore/cli.py` — `cmd_ui()` CLI command (modified)
- `src/lore/store/memory.py` — `list_all_entity_mentions()` override (fixed during QA)

### Frontend (JavaScript)
- `src/lore/ui/src/index.js` — Main entry point
- `src/lore/ui/src/state.js` — AppState with EventTarget pub/sub, URL sync
- `src/lore/ui/src/api.js` — API client with LRU cache
- `src/lore/ui/src/colors.js` — Color palette from PRD
- `src/lore/ui/src/utils.js` — debounce, truncateText, formatDate
- `src/lore/ui/src/graph/simulation.js` — D3-force simulation
- `src/lore/ui/src/graph/renderer.js` — Canvas 2D rendering
- `src/lore/ui/src/graph/interaction.js` — Zoom, pan, drag, hover, context menu, keyboard
- `src/lore/ui/src/graph/layout.js` — Layout mode manager (force/cluster)
- `src/lore/ui/src/panels/detail.js` — Detail panel
- `src/lore/ui/src/panels/filters.js` — Filter sidebar
- `src/lore/ui/src/panels/stats.js` — Stats dashboard
- `src/lore/ui/src/components/search.js` — Search bar
- `src/lore/ui/src/components/timeline.js` — Timeline scrubber
- `src/lore/ui/src/components/minimap.js` — Minimap
- `src/lore/ui/dist/index.html` — Built HTML with CSS (13.6KB)
- `src/lore/ui/dist/app.js` — Built JS bundle (99.7KB)

### Tests
- `tests/server/test_ui_routes.py` — 45 tests (34 original + 11 added)
- `tests/test_cli_ui.py` — 7 tests
