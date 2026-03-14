# E1: Graph Visualization — Sprint Stories

**Epic:** E1 — Graph Visualization (Web UI)
**Sprint Target:** v0.11.0
**Date:** March 14, 2026
**Author:** Bob (Scrum Master, BMAD)
**Architecture:** [e1-graph-visualization-architecture.md](./e1-graph-visualization-architecture.md)
**PRD:** [e1-graph-visualization-prd.md](./e1-graph-visualization-prd.md)

---

## Sprint Summary

| Phase | Stories | Total Points | Focus |
|-------|---------|-------------|-------|
| Phase 1: Foundation (MVP) | S1–S8 | 8 stories, ~28h | Backend API + CLI + Core graph rendering |
| Phase 2: Interactivity | S9–S14 | 6 stories, ~22h | Filters, search, tooltips, URL sync |
| Phase 3: Polish | S15–S21 | 7 stories, ~28h | Clusters, timeline, stats, minimap, responsive |
| **Total** | **21 stories** | **~78h** | |

### Parallelization Batches

```
Batch 1 (no deps):        S1, S2, S3           [backend + build + frontend skeleton]
Batch 2 (needs S1):       S4, S5               [backend API detail + CLI]
Batch 3 (needs S2,S3):    S6                   [frontend graph rendering]
Batch 4 (needs S4,S5,S6): S7, S8              [frontend interactions + detail panel]
Batch 5 (needs S7,S8):    S9, S10, S11        [filters, search API, hover/tooltips]
Batch 6 (needs S9-S11):   S12, S13, S14       [search UI, URL sync, context menu]
Batch 7 (needs S1):       S15, S16, S17       [cluster/stats/timeline APIs]
Batch 8 (needs S15-S17):  S18, S19, S20, S21  [cluster/timeline/stats/minimap UI + responsive]
```

---

## Phase 1: Foundation (MVP)

### S1: Store Interface Extensions for Graph Data

**Size:** M (3h)
**Dependencies:** None
**Files:** `src/lore/store/base.py`, `src/lore/store/sqlite.py`, `tests/store/test_ui_queries.py`

**Description:**
Add `list_entities()`, `list_relationships()`, and `list_entity_mentions()` methods to the Store ABC and SQLite implementation. These are needed by the `/v1/ui/graph` endpoint to assemble the full graph dataset. The underlying tables already exist from graph feature work — we just need query methods.

**Acceptance Criteria:**
- [ ] `Store.list_entities(entity_type=None, limit=None)` returns `List[Entity]`
- [ ] `Store.list_relationships(active_only=True)` returns `List[Relationship]`
- [ ] `Store.list_entity_mentions(memory_ids=None)` returns `List[EntityMention]`
- [ ] SQLite implementation queries existing tables
- [ ] Methods handle empty results gracefully
- [ ] Filters (entity_type, active_only, memory_ids) work correctly

**Test Scenarios:**
1. `list_entities()` returns all entities; with `entity_type="tool"` returns only tools
2. `list_relationships(active_only=True)` excludes inactive relationships
3. `list_entity_mentions(memory_ids=["m1","m2"])` returns only mentions for those memories
4. `list_entity_mentions(memory_ids=[])` returns empty list (not all mentions)
5. All three methods return empty lists when no data exists

---

### S2: Frontend Build Pipeline (esbuild + package.json)

**Size:** S (2h)
**Dependencies:** None
**Files:** `src/lore/ui/package.json`, `src/lore/ui/build.mjs`, `src/lore/ui/src/index.js` (stub), `src/lore/ui/dist/index.html`, `src/lore/ui/static/favicon.svg`

**Description:**
Set up the frontend build toolchain: package.json with D3 subset + esbuild as dev dependencies, build script that bundles `src/` → `dist/app.js`, and the `index.html` shell with inlined CSS for the dark theme layout (header, filter sidebar, graph canvas, detail panel, timeline — all as empty containers). Built files checked into git.

**Acceptance Criteria:**
- [ ] `npm install` installs only dev deps (esbuild, d3-force, d3-quadtree, d3-zoom, d3-scale, d3-selection)
- [ ] `node build.mjs` produces `dist/app.js` in <1s
- [ ] `dist/index.html` has CSS grid layout with all panels (filters, graph, detail, timeline, header)
- [ ] Dark theme CSS variables from PRD Section 5 applied
- [ ] `index.js` stub initializes without errors
- [ ] Total bundle (HTML + JS) < 100KB at this stage
- [ ] Brain emoji SVG favicon works

**Test Scenarios:**
1. `node build.mjs` succeeds and produces output
2. `dist/index.html` opens in browser without JS errors
3. CSS grid layout renders header, sidebar, canvas container, collapsed detail panel
4. `npm run build` is defined in package.json and works

---

### S3: Color Palette + Utility Modules

**Size:** S (1h)
**Dependencies:** None
**Files:** `src/lore/ui/src/colors.js`, `src/lore/ui/src/utils.js`

**Description:**
Implement the color constants from PRD Section 5 (memory type colors, entity type colors, background/panel colors) and utility functions (debounce, formatDate, truncateText). These are shared dependencies for all frontend modules.

**Acceptance Criteria:**
- [ ] `MEMORY_COLORS` maps all 9 memory types to hex colors from PRD
- [ ] `ENTITY_COLORS` maps all 10 entity types to hex colors from PRD
- [ ] `getNodeColor(node)` returns correct color based on `node.kind` and `node.type`
- [ ] `debounce(fn, ms)` delays execution, cancels on re-invocation
- [ ] `truncateText(str, max)` truncates with ellipsis at `max` chars
- [ ] `formatDate(isoString)` returns human-readable date

**Test Scenarios:**
1. `getNodeColor({kind:'memory', type:'code'})` returns `#4ade80`
2. `getNodeColor({kind:'entity', type:'person'})` returns `#fcd34d`
3. Unknown types fall back to gray (`#9ca3af`)
4. `debounce` calls function only after delay, resets on repeated calls
5. `truncateText("hello world", 5)` returns `"hello…"`

---

### S4: Backend Graph API Endpoint (`/v1/ui/graph`)

**Size:** L (6h)
**Dependencies:** S1 (store methods)
**Files:** `src/lore/server/routes/ui.py`, `tests/server/test_ui_routes.py`

**Description:**
Implement the primary `GET /v1/ui/graph` endpoint that assembles the complete visualization dataset. Fetches entities, relationships, memory metadata (no content), and entity mentions. Returns `{nodes, edges, stats}` in the format specified by the PRD. Supports query params for server-side filtering (project, type, tier, date range, min_importance, limit).

**Acceptance Criteria:**
- [ ] Returns `{nodes: [], edges: [], stats: {}}` structure
- [ ] Memory nodes have: id, kind="memory", label (first 60 chars), type, tier, project, importance, confidence, tags, created_at, upvotes, downvotes, access_count
- [ ] Entity nodes have: id, kind="entity", label, type, mention_count, aliases, first_seen_at, last_seen_at
- [ ] Entity↔entity edges from relationships with rel_type, weight, label
- [ ] Memory↔entity edges from entity_mentions with rel_type="mentions"
- [ ] Stats block includes total_memories, total_entities, total_relationships, filtered_nodes, filtered_edges
- [ ] `?project=X` filters memory nodes by project
- [ ] `?min_importance=0.5` filters memories by importance score
- [ ] `?since=` and `?until=` filter by date range
- [ ] `?limit=N` caps number of memory nodes
- [ ] `?include_orphans=false` excludes memories with no edges
- [ ] Memory content is NOT included (lazy-loaded on click)

**Test Scenarios:**
1. Empty database returns `{nodes:[], edges:[], stats:{total_memories:0, ...}}`
2. Seeded DB returns correct node/edge counts
3. `?project=auth` returns only memories from that project + all entities
4. `?min_importance=0.8` excludes low-importance memories
5. `?limit=5` returns at most 5 memory nodes
6. Entity↔entity edges only include edges where both endpoints are in response
7. Memory↔entity edges only include edges where both endpoints are in response
8. Stats `filtered_nodes` matches actual `len(nodes)`
9. `?include_orphans=false` excludes memories with zero edges

---

### S5: CLI Command (`lore ui`) + UI App Factory

**Size:** M (3h)
**Dependencies:** S1 (store methods), S4 (routes)
**Files:** `src/lore/server/ui_app.py`, `src/lore/cli.py` (modify), `tests/test_cli_ui.py`

**Description:**
Create `create_ui_app()` factory that builds a standalone FastAPI app with UI routes + static file serving. Add `lore ui` CLI command that starts this app, opens the browser, and serves the frontend. Store-agnostic: works with both SQLite and Postgres via the Lore facade.

**Acceptance Criteria:**
- [ ] `create_ui_app(static_dir)` returns a FastAPI app with GZip middleware, UI router, and static file mount
- [ ] `lore ui` starts server on `127.0.0.1:8766` by default
- [ ] `lore ui --port 3333` uses custom port
- [ ] `lore ui --no-open` skips browser opening
- [ ] `lore ui --host 0.0.0.0` warns about security
- [ ] Server serves `index.html` at root `/`
- [ ] API endpoints accessible at `/v1/ui/*`
- [ ] Ctrl+C gracefully shuts down
- [ ] Error message if UI assets not found in dist directory
- [ ] Works with SQLite store (no Postgres required)

**Test Scenarios:**
1. `CliRunner.invoke(ui, ["--no-open"])` starts without error (short timeout)
2. `create_ui_app()` returns a FastAPI app with `/v1/ui/graph` route
3. Static files served from dist directory
4. Missing dist directory produces clear error message
5. `--host 0.0.0.0` prints security warning

---

### S6: D3 Force Simulation + Canvas Renderer

**Size:** L (6h)
**Dependencies:** S2 (build pipeline), S3 (colors)
**Files:** `src/lore/ui/src/graph/simulation.js`, `src/lore/ui/src/graph/renderer.js`, `src/lore/ui/src/state.js`

**Description:**
Implement the core rendering pipeline. `AppState` class with EventTarget for pub/sub. D3-force simulation with forceLink, forceManyBody, forceCenter, forceCollide. Canvas 2D rendering loop that draws nodes (circles for memories, hexagons for entities), edges (lines with arrowheads), and labels. Node size mapped from importance/mention_count. Colors from `colors.js`. Level-of-detail: labels only at zoom > 0.8, edges hidden when zoomed out on large graphs.

**Acceptance Criteria:**
- [ ] `AppState` extends EventTarget, holds nodes/edges/filters/selection state
- [ ] `AppState.setFilter()` recomputes `filteredNodeIds` and dispatches `filterChange`
- [ ] `AppState.selectNode()` dispatches `selectionChange`
- [ ] Force simulation configures: forceLink (distance by weight), forceManyBody (-30 strength), forceCenter, forceCollide
- [ ] Simulation cools down and stops (no idle CPU burn)
- [ ] Canvas renders memory nodes as circles, sized by importance (8px–24px)
- [ ] Canvas renders entity nodes as hexagons, sized by mention_count (6px–30px)
- [ ] Node colors match `colors.js` palette by type
- [ ] Edges drawn as lines with arrowheads, colored by source, 40% opacity
- [ ] Filtered-out nodes render at 10% opacity
- [ ] Labels render only when `transform.k > 0.8`
- [ ] Labels truncated at 16 characters
- [ ] Selected node gets gold border + glow
- [ ] Tier encoded as border style (working=dashed, short=dotted, long=solid)
- [ ] Confidence mapped to node opacity (0.3–1.0)

**Test Scenarios:**
1. Simulation starts, ticks, and eventually stops (alpha < 0.001)
2. `AppState` dispatches `filterChange` on setFilter
3. `AppState` dispatches `selectionChange` on selectNode
4. `_recomputeFiltered()` correctly filters by project, type, tier, importance, date range
5. Mock canvas receives correct draw calls for circles and hexagons

---

### S7: Zoom, Pan, Drag Interactions

**Size:** M (3h)
**Dependencies:** S6 (renderer + simulation)
**Files:** `src/lore/ui/src/graph/interaction.js`

**Description:**
Implement mouse/touch interactions on the canvas. D3-zoom for zoom/pan transforms. Quadtree-based hit testing for O(log n) node lookup. Click to select, drag to reposition (pin node), scroll to zoom, click+drag background to pan. `centerOnNode(id)` for programmatic navigation with smooth transition.

**Acceptance Criteria:**
- [ ] Scroll wheel zooms in/out (scale extent 0.1–8x)
- [ ] Click+drag on background pans the view
- [ ] Click on a node selects it (dispatches `selectionChange`)
- [ ] Click on empty space deselects current node
- [ ] Drag a node repositions it and pins it (fx/fy set)
- [ ] Quadtree rebuilt on every simulation tick for accurate hit testing
- [ ] `centerOnNode(id)` smoothly animates (500ms) to center the node at 2x zoom
- [ ] Touch: pinch zoom/pan via D3-zoom's built-in touch support
- [ ] Cursor changes: pointer on node hover, grab on drag, default otherwise

**Test Scenarios:**
1. Quadtree `find()` returns nearest node within 20px radius
2. `centerOnNode()` calculates correct transform for node position
3. Drag sets `fx`/`fy` on node (pinned)
4. Click on empty space (no quadtree hit) clears selection
5. Zoom transform is bounded between 0.1 and 8

---

### S8: Detail Panel (Memory + Entity Views)

**Size:** M (4h)
**Dependencies:** S6 (state), S7 (click interaction)
**Files:** `src/lore/ui/src/panels/detail.js`, `src/lore/ui/src/api.js`

**Description:**
Implement the right-side detail panel that opens when a node is clicked. Lazy-loads full memory content or entity details via `/v1/ui/memory/{id}` or `/v1/ui/entity/{id}`. Renders memory content as markdown (minimal inline renderer). Shows connected entities/memories as clickable links. Panel slides in/out with animation. Close on button click or Escape key. API client with LRU cache (max 200 entries).

Backend: Implement `GET /v1/ui/memory/{id}` and `GET /v1/ui/entity/{id}` endpoints.

**Acceptance Criteria:**
- [ ] `GET /v1/ui/memory/{id}` returns full content, type, tier, project, tags, importance, confidence, upvotes, downvotes, access_count, created_at, updated_at, source, connected_entities, connected_memories
- [ ] `GET /v1/ui/entity/{id}` returns name, entity_type, aliases, description, mention_count, first_seen_at, last_seen_at, connected_entities, connected_memories
- [ ] Both endpoints return 404 for unknown IDs
- [ ] Click memory node → panel shows full content rendered as markdown
- [ ] Click entity node → panel shows entity details with connected items
- [ ] Connected items are clickable → navigates graph to that node + opens its detail
- [ ] Panel slides in from right (250ms ease-out)
- [ ] Close button and Escape key close panel
- [ ] API client caches responses in `Map` with LRU eviction at 200 entries
- [ ] Loading state shown while fetching content
- [ ] Error state shown if fetch fails

**Test Scenarios:**
1. `GET /v1/ui/memory/{known_id}` returns correct content and connections
2. `GET /v1/ui/memory/{unknown_id}` returns 404
3. `GET /v1/ui/entity/{known_id}` returns correct details and connections
4. API client cache hit avoids duplicate fetch
5. Cache evicts oldest entry when exceeding 200
6. Panel renders markdown bold, italic, code blocks, links correctly
7. Clicking connected entity link dispatches `selectionChange` with that entity's ID

---

## Phase 2: Interactivity

### S9: Filter Sidebar

**Size:** M (4h)
**Dependencies:** S6 (state), S8 (detail panel establishes panel pattern)
**Files:** `src/lore/ui/src/panels/filters.js`, modify `src/lore/ui/src/state.js`

**Description:**
Implement the left-side filter sidebar. Dynamically populates filter options from the loaded graph data: project multi-select dropdown, memory type checkboxes, entity type checkboxes, tier checkboxes, importance slider, date range pickers. Filters apply instantly via `state.setFilter()` with debounce. Active filter count shown as badge. "Reset filters" button clears all. Sidebar collapsible.

**Acceptance Criteria:**
- [ ] Project dropdown lists all unique projects from loaded nodes
- [ ] Memory type checkboxes for all types present in data
- [ ] Entity type checkboxes for all entity types present in data
- [ ] Tier checkboxes (working, short, long)
- [ ] Importance slider (0.0–1.0) with current value display
- [ ] Date range: start and end date pickers
- [ ] All filters apply instantly (debounced 100ms for slider)
- [ ] Filtered-out nodes fade to 10% opacity in graph (not removed)
- [ ] Badge shows count of active filters
- [ ] "Reset filters" clears all filters to defaults
- [ ] Sidebar collapses to icon bar (toggle button)
- [ ] Multiple filters combine with AND logic

**Test Scenarios:**
1. Setting project filter updates `filteredNodeIds` to only matching memories
2. Importance slider at 0.5 filters out nodes below 0.5
3. Multiple filters combine: project=X AND type=code
4. Reset clears all filters, `filteredNodeIds` contains all node IDs
5. Badge shows "3" when 3 filters are active
6. Entity nodes are not filtered by memory-specific filters (project, tier)

---

### S10: Search API Endpoint (`/v1/ui/search`)

**Size:** M (3h)
**Dependencies:** S4 (ui routes module exists)
**Files:** `src/lore/server/routes/ui.py` (add endpoint), `tests/server/test_ui_routes.py`

**Description:**
Implement `POST /v1/ui/search` endpoint that searches memories and entities. Supports keyword and semantic search modes. Leverages existing search pipeline (`POST /v1/memories/search`). Returns results with relevance scores, limited to `limit` results. Supports filter params (project, type, tier, date range, min_importance).

**Acceptance Criteria:**
- [ ] Accepts JSON body: `{query, mode, limit, filters}`
- [ ] `mode="keyword"` does text search on memory content
- [ ] `mode="semantic"` uses embedding-based similarity (if embeddings available, falls back to keyword)
- [ ] Returns `{results: [{id, kind, label, type, score, importance, project}], total, query_time_ms}`
- [ ] Results sorted by score descending
- [ ] `limit` caps result count (default 20)
- [ ] Filters narrow search scope
- [ ] Returns both memory and entity matches
- [ ] `query_time_ms` is accurate

**Test Scenarios:**
1. Keyword search for "redis" returns memories containing "redis"
2. Empty query returns empty results (not all memories)
3. `limit=5` returns at most 5 results
4. Results include `score` field
5. `query_time_ms` is a positive number
6. Project filter limits results to that project
7. Unknown mode returns 400 error

---

### S11: Hover Tooltips + Edge Labels

**Size:** S (2h)
**Dependencies:** S7 (interaction module)
**Files:** `src/lore/ui/src/graph/interaction.js` (modify), `src/lore/ui/src/graph/renderer.js` (modify)

**Description:**
Show tooltip on node hover (name, type, importance/mention_count). Show edge relationship type label on edge hover. Tooltip follows mouse position. Edge hover uses proximity detection (closest edge within 5px of mouse).

**Acceptance Criteria:**
- [ ] Hovering a memory node shows tooltip: label, type, importance score
- [ ] Hovering an entity node shows tooltip: name, entity type, mention count
- [ ] Tooltip positioned near mouse, stays within viewport bounds
- [ ] Tooltip disappears when mouse leaves node
- [ ] Hovering an edge shows relationship type label at hover position
- [ ] Edge hover detection uses perpendicular distance (≤5px)
- [ ] Hovered node gets subtle highlight (brighter color)
- [ ] Hovered edge brightens to full opacity

**Test Scenarios:**
1. Mouse over node → tooltip rendered with correct content
2. Mouse away → tooltip removed
3. Tooltip doesn't overflow viewport edges
4. Edge hover correctly identifies nearest edge within threshold
5. No tooltip when hovering empty space

---

### S12: Search Bar UI + Result Highlighting

**Size:** M (3h)
**Dependencies:** S10 (search API), S6 (state/renderer)
**Files:** `src/lore/ui/src/components/search.js`

**Description:**
Search bar in the header. Debounced input (300ms) calls `/v1/ui/search`. Results displayed in dropdown list with relevance scores. Matching nodes in graph get pulsing glow effect. Non-matching nodes dim. Click a result → graph centers on that node and opens detail panel. Escape or clear clears search results.

**Acceptance Criteria:**
- [ ] Search input in header, prominently placed
- [ ] Input debounced at 300ms before API call
- [ ] Results dropdown shows: label, type, score (as percentage)
- [ ] Matching node IDs stored in `state.searchResults`
- [ ] Renderer applies pulsing glow to search matches (1.5s ease-in-out infinite)
- [ ] Non-matching nodes dim to 20% opacity during active search
- [ ] Click result → `centerOnNode(id)` + `selectNode(id)`
- [ ] Escape key or clear button clears search results and restores normal view
- [ ] "No results" message when search returns empty
- [ ] Loading spinner during search

**Test Scenarios:**
1. Typing triggers search after 300ms debounce
2. Rapid typing resets debounce (only last query fires)
3. Results list renders with scores
4. Clicking result dispatches centerOnNode and selectionChange
5. Escape clears searchResults and restores all nodes to normal opacity
6. Empty search input clears results

---

### S13: URL State Sync (Bookmarkable Filters)

**Size:** S (2h)
**Dependencies:** S9 (filters)
**Files:** `src/lore/ui/src/state.js` (modify), `src/lore/ui/src/index.js` (modify)

**Description:**
Serialize filter state to URL query parameters via `history.replaceState()`. On page load, read URL params and apply as initial filters. This makes filter configurations shareable and bookmarkable.

**Acceptance Criteria:**
- [ ] Filter changes update URL without page reload
- [ ] URL params include: project, type, entity_type, tier, min_importance, since, until, search
- [ ] Page load with URL params restores filter state
- [ ] Empty/default filters produce clean URL (no params)
- [ ] Selected node ID optionally in URL hash (`#node=mem_abc123`)
- [ ] Browser back/forward navigates filter history

**Test Scenarios:**
1. Setting project filter adds `?project=auth` to URL
2. Loading `?min_importance=0.5` sets slider to 0.5
3. Multiple filters combine: `?project=auth&type=code`
4. Reset filters clears URL params
5. `#node=mem_abc123` on load selects and centers that node

---

### S14: Right-Click Context Menu

**Size:** S (2h)
**Dependencies:** S7 (interaction)
**Files:** `src/lore/ui/src/graph/interaction.js` (modify)

**Description:**
Right-click a node shows context menu with actions: Copy ID, View in Terminal (copies `lore recall` command), Forget (confirmation dialog). Context menu positioned at click point, dismisses on click-away or Escape. For v1, actions are read-only except Copy ID.

**Acceptance Criteria:**
- [ ] Right-click on node shows context menu
- [ ] Menu items: "Copy ID", "Copy Recall Command", "View Details"
- [ ] "Copy ID" copies `node.id` to clipboard
- [ ] "Copy Recall Command" copies `lore recall --id <id>` to clipboard
- [ ] "View Details" opens detail panel for that node
- [ ] Menu dismisses on click-away, Escape, or item selection
- [ ] Menu positioned at click point, stays within viewport
- [ ] Right-click on empty space does nothing (browser default)
- [ ] Long-press on touch devices triggers context menu (500ms)

**Test Scenarios:**
1. Right-click node → menu appears at click position
2. Clicking "Copy ID" copies correct ID to clipboard
3. Click-away dismisses menu
4. Escape dismisses menu
5. Menu doesn't overflow viewport edges
6. No context menu on empty canvas right-click

---

## Phase 3: Polish

### S15: Cluster API + Stats API + Timeline API

**Size:** L (5h)
**Dependencies:** S1 (store methods), S4 (ui routes module)
**Files:** `src/lore/server/routes/ui.py` (add endpoints), `tests/server/test_ui_routes.py`

**Description:**
Implement three backend endpoints:
- `GET /v1/ui/graph/clusters` — returns nodes grouped by project/type with cluster metadata
- `GET /v1/ui/stats` — returns aggregate statistics (totals, breakdowns, top entities, recent activity)
- `GET /v1/ui/timeline` — returns memory density over time in configurable buckets (hour/day/week/month)

**Acceptance Criteria:**
- [ ] `/v1/ui/graph/clusters?group_by=project` returns clusters with node_ids, labels, counts
- [ ] `/v1/ui/graph/clusters?group_by=type` groups by memory type
- [ ] Cluster response includes full `nodes` and `edges` arrays
- [ ] `/v1/ui/stats` returns: total_memories, total_entities, total_relationships, by_type, by_project, by_tier, by_entity_type, avg_importance, top_entities (top 5), recent_24h, recent_7d, oldest_memory, newest_memory
- [ ] `/v1/ui/timeline?bucket=day` returns `{buckets: [{date, count, by_type}], range: {start, end}}`
- [ ] Timeline supports bucket sizes: hour, day, week, month
- [ ] Stats and timeline respect project filter param
- [ ] All three endpoints handle empty database gracefully

**Test Scenarios:**
1. Clusters group correctly by project — each cluster's node_ids are all in that project
2. Clusters group correctly by type
3. Stats totals match actual database counts
4. Stats `top_entities` sorted by mention_count descending
5. Stats `recent_24h` counts only memories from last 24 hours
6. Timeline with daily buckets returns one entry per day with non-zero memories
7. Timeline `range` matches oldest and newest memory dates
8. Empty database returns empty clusters, zero stats, empty timeline

---

### S16: Cluster View (Frontend)

**Size:** M (4h)
**Dependencies:** S15 (cluster API), S6 (simulation/renderer)
**Files:** `src/lore/ui/src/graph/layout.js`

**Description:**
Add layout mode toggle: Force Layout / Cluster by Project / Cluster by Type. Cluster view positions node groups together with convex hull boundaries around each cluster. Cluster labels visible. Smooth transition animation when switching layouts (500ms). Click a cluster hull → zoom into that group.

**Acceptance Criteria:**
- [ ] Toggle buttons in header: "Force" / "By Project" / "By Type"
- [ ] Switching layout smoothly transitions node positions (500ms)
- [ ] Cluster mode fetches data from `/v1/ui/graph/clusters`
- [ ] Convex hull drawn around each cluster's nodes (semi-transparent fill + border)
- [ ] Cluster label positioned at cluster center
- [ ] Cluster colors consistent with node type colors
- [ ] Click hull → zoom to fit cluster bounds
- [ ] Switching back to Force layout restores force simulation
- [ ] `state.viewMode` tracks current layout ('force', 'cluster-project', 'cluster-type')

**Test Scenarios:**
1. Toggle to "By Project" calls cluster API with `group_by=project`
2. Convex hull contains all cluster nodes
3. Toggle back to "Force" restores force simulation
4. `state.viewMode` updates on toggle
5. Cluster labels render at cluster center position

---

### S17: Timeline Scrubber (Frontend)

**Size:** M (4h)
**Dependencies:** S15 (timeline API), S6 (state)
**Files:** `src/lore/ui/src/components/timeline.js`

**Description:**
Bottom bar with timeline scrubber. Shows memory density as a mini bar chart (bars per bucket). Draggable range handles to select date range. Selecting a range applies a date filter → graph shows only memories in that range. Nodes animate in/out as range changes. Collapsible.

**Acceptance Criteria:**
- [ ] Timeline bar at bottom of screen, 48px tall
- [ ] Bar chart shows memory count per bucket (day by default)
- [ ] Two draggable handles to select start/end of date range
- [ ] Dragging handles updates `state.filters.dateRange`
- [ ] Nodes outside date range fade to 10% opacity
- [ ] Timeline range spans from oldest to newest memory
- [ ] Bars colored by memory type distribution
- [ ] Timeline is collapsible (toggle button)
- [ ] Hidden on mobile (<768px)

**Test Scenarios:**
1. Timeline renders bars matching API bucket data
2. Dragging start handle updates dateRange[0] in state
3. Date range filter correctly includes/excludes nodes
4. Full-width drag = all dates = no filter
5. Collapse toggle hides timeline

---

### S18: Stats Dashboard Panel

**Size:** M (3h)
**Dependencies:** S15 (stats API)
**Files:** `src/lore/ui/src/panels/stats.js`

**Description:**
Stats panel toggled from header button. Shows aggregate statistics: totals (memories, entities, relationships), pie/donut chart for memories by type, bar chart for memories by project, average importance, top 5 most-connected entities, recent activity counts. Stats update when filters change (re-fetch with current project filter).

**Acceptance Criteria:**
- [ ] Stats button in header toggles stats panel overlay
- [ ] Total counts displayed prominently: memories, entities, relationships
- [ ] Donut chart for memory type distribution (using canvas)
- [ ] Bar chart for top projects by memory count (using canvas)
- [ ] Average importance score displayed
- [ ] Top 5 entities listed with mention counts (clickable → navigate)
- [ ] Recent activity: "15 memories in last 24h, 67 in last 7d"
- [ ] Stats re-fetch when project filter changes
- [ ] Panel close button + Escape

**Test Scenarios:**
1. Stats panel shows correct totals from API response
2. Donut chart segments match `by_type` proportions
3. Clicking a top entity navigates to that entity node
4. Changing project filter re-fetches stats for that project
5. Panel closes on Escape

---

### S19: Minimap

**Size:** S (2h)
**Dependencies:** S6 (renderer), S7 (zoom/pan)
**Files:** `src/lore/ui/src/components/minimap.js`

**Description:**
Small canvas (150x150px) in the bottom-left corner showing an overview of the entire graph. All nodes as 2px dots colored by type. White rectangle showing the current viewport bounds. Click on minimap → pan main view to that location. Filtered nodes shown at low opacity.

**Acceptance Criteria:**
- [ ] 150x150px canvas in bottom-left corner
- [ ] All nodes rendered as 2px colored dots
- [ ] Viewport rectangle (white border) shows visible area
- [ ] Viewport rectangle updates as user zooms/pans
- [ ] Click on minimap pans main canvas to that position
- [ ] Filtered-out nodes at 10% opacity in minimap
- [ ] Minimap auto-scales to fit all node positions
- [ ] Hidden on mobile (<768px)

**Test Scenarios:**
1. Minimap renders all nodes within 150x150 bounds
2. Viewport rectangle size reflects zoom level (larger rect = more zoomed out)
3. Panning main view updates viewport rectangle position
4. Click on minimap corner pans main view to match
5. Node colors match main canvas colors

---

### S20: Responsive Layout + Touch Support

**Size:** M (4h)
**Dependencies:** S9 (filters), S8 (detail panel), S17 (timeline)
**Files:** CSS in `src/lore/ui/dist/index.html`, `src/lore/ui/src/graph/interaction.js` (modify)

**Description:**
Implement responsive breakpoints from architecture Section 9. Desktop: three-column layout. Tablet (768–1024px): filter sidebar collapses to 48px icon bar, detail panel overlays graph. Mobile (<768px): graph fullscreen, filters and detail as bottom sheets, timeline hidden. Touch: tap=click, long-press=context menu, pinch zoom/pan already handled by D3-zoom.

**Acceptance Criteria:**
- [ ] Desktop (>1024px): Full three-column layout as designed
- [ ] Tablet (768–1024px): Filter sidebar collapses to icon-only (48px)
- [ ] Tablet: Detail panel overlays graph with shadow (position: fixed)
- [ ] Mobile (<768px): Graph fills screen
- [ ] Mobile: Filter panel as bottom sheet (slides up, max 60vh, rounded top corners)
- [ ] Mobile: Detail panel as bottom sheet
- [ ] Mobile: Timeline hidden
- [ ] Mobile: Minimap hidden
- [ ] Canvas resizes properly on window resize (debounced)
- [ ] Touch: tap selects node
- [ ] Touch: long-press (500ms) triggers context menu
- [ ] Touch: pinch zoom works (D3-zoom built-in)

**Test Scenarios:**
1. At 800px width, filter sidebar shows icons only
2. At 600px width, filters are a bottom sheet
3. Window resize recalculates canvas dimensions
4. Touch tap on node triggers selection
5. Long press triggers context menu at touch position

---

### S21: Keyboard Navigation + Accessibility

**Size:** M (3h)
**Dependencies:** S8 (detail panel), S9 (filters)
**Files:** `src/lore/ui/src/graph/interaction.js` (modify), `src/lore/ui/dist/index.html` (modify)

**Description:**
Keyboard navigation for node selection and panel interactions. Tab cycles through nodes (by importance order). Enter opens detail panel. Escape closes panels/search/menus. Arrow keys pan the graph. +/- zoom. ARIA live regions announce selected node. High contrast mode toggle.

**Acceptance Criteria:**
- [ ] Tab key cycles through nodes (ordered by importance descending)
- [ ] Shift+Tab cycles in reverse
- [ ] Enter on focused node opens detail panel
- [ ] Escape closes: context menu → search → detail panel → filters (in priority order)
- [ ] Arrow keys pan the graph (50px per press)
- [ ] +/- keys zoom in/out
- [ ] ARIA live region announces: "Selected: [node label], [type]" on selection
- [ ] Detail panel content is screen-reader accessible
- [ ] Filter controls are keyboard-navigable
- [ ] High contrast mode toggle in header settings
- [ ] High contrast mode increases border widths, uses white/black only for critical UI

**Test Scenarios:**
1. Tab key selects next node (highest importance first)
2. Enter opens detail panel for focused node
3. Escape closes open detail panel
4. Arrow keys update transform (pan)
5. ARIA live region text updates on node selection
6. High contrast toggle applies CSS class to body

---

## Dependency Graph

```
S1 (Store)  ──────────┬──────────────────────────────────┐
                       │                                   │
S2 (Build)  ───┐      │                                   │
               │      ▼                                   ▼
S3 (Colors) ───┼──▶ S6 (Simulation+Renderer)  S4 (Graph API) ──▶ S5 (CLI+App)
               │      │                           │
               │      ▼                           │
               │    S7 (Interactions) ◀───────────┘
               │      │
               │      ▼
               │    S8 (Detail Panel + Memory/Entity APIs)
               │      │
               │      ├──────────────┬───────────────┐
               │      ▼              ▼               ▼
               │    S9 (Filters)   S10 (Search API)  S11 (Tooltips)
               │      │              │               │
               │      ▼              ▼               ▼
               │    S13 (URL Sync) S12 (Search UI)  S14 (Context Menu)
               │
               │    S15 (Cluster+Stats+Timeline APIs)
               │      │
               │      ├────────────┬──────────────┐
               │      ▼            ▼              ▼
               │    S16 (Clusters) S17 (Timeline) S18 (Stats)
               │                                   │
               │    S19 (Minimap) ◀────────────────┘
               │
               └──▶ S20 (Responsive) ──▶ S21 (Accessibility)
```

---

## Critical Path

The critical path through the dependency graph is:

**S1 → S4 → S5 → S6 → S7 → S8 → S9 → S12 → S20 → S21**

This represents the minimum sequence to go from no code to fully interactive UI:
Store methods → Graph API → CLI/Server → Rendering → Interactions → Detail Panel → Filters → Search → Responsive → A11y

**Estimated critical path duration: ~38h** (with parallelization of non-critical items, total wall-clock can be reduced)

---

## Definition of Done (All Stories)

1. Code implemented and builds without errors
2. All acceptance criteria met
3. Tests written and passing (unit + integration as applicable)
4. No lint errors
5. `dist/` updated if frontend files changed (run `node build.mjs`)
6. Bundle size verified < 500KB (for frontend stories)
