# E1: Graph Visualization — Technical Architecture Document

**Version:** 1.0
**Epic:** E1 — Graph Visualization (Web UI)
**Date:** March 14, 2026
**Author:** Winston (Solutions Architect, BMAD)
**Status:** Draft
**Implements:** [E1 PRD v1.0](./e1-graph-visualization-prd.md)

---

## 1. Architecture Overview

### Design Philosophy

**Zero-dependency frontend, maximum leverage of existing backend.**

Lore already has a graph traversal engine (`GraphTraverser`), D3-compatible serialization (`to_d3_json()`), entity caching (`EntityCache`), and a FastAPI server with 11 route modules. The architecture adds a thin API layer, a vanilla JS frontend using D3-force + Canvas, and a CLI entry point. No React, no build toolchain at install time, no npm in the user's environment.

### System Context Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│  User's Machine                                                     │
│                                                                     │
│  ┌──────────┐     ┌──────────────────────────────────────────────┐  │
│  │  Browser  │────▶│  FastAPI Server (localhost:8766)             │  │
│  │          │◀────│                                              │  │
│  │  D3.js   │     │  ┌────────────────┐  ┌──────────────────┐   │  │
│  │  Canvas   │     │  │ /v1/ui/* routes │  │ StaticFiles mount│   │  │
│  │  Vanilla  │     │  │ (ui.py)        │  │ (/ui/dist/)      │   │  │
│  │  JS       │     │  └───────┬────────┘  └──────────────────┘   │  │
│  └──────────┘     │          │                                   │  │
│                    │  ┌───────▼────────────────────────────────┐  │  │
│                    │  │  Lore Core                              │  │  │
│                    │  │  ┌──────────┐ ┌───────────┐ ┌────────┐ │  │  │
│                    │  │  │GraphTrav.│ │EntityCache│ │Store   │ │  │  │
│                    │  │  │to_d3_json│ │EntityMgr  │ │(SQLite │ │  │  │
│                    │  │  │          │ │           │ │ or Pg) │ │  │  │
│                    │  │  └──────────┘ └───────────┘ └────────┘ │  │  │
│                    │  └────────────────────────────────────────┘  │  │
│                    └──────────────────────────────────────────────┘  │
│                                                                     │
│  ┌──────────┐                                                       │
│  │ lore ui  │ ── starts server, opens browser                       │
│  └──────────┘                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

### Key Architectural Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| AD-1 | Vanilla JS, no framework | Read-only viz, <500KB budget, no component lifecycle needed |
| AD-2 | D3-force + Canvas renderer | Battle-tested at 5K nodes, `to_d3_json()` already exists |
| AD-3 | esbuild for bundling (dev only) | <100ms builds, 8MB binary, zero node_modules. Built files checked into git — `pip install` users never run npm |
| AD-4 | Single FastAPI server for both API and static files | Reuses existing server infra, one process to manage |
| AD-5 | Client-side filtering, server-side data fetch | Filters apply at 60fps without network round-trips; server just ships the dataset |
| AD-6 | Canvas for rendering, quadtree for hit testing | SVG dies at 500 nodes. Canvas + D3 quadtree handles 5K+ at 60fps |
| AD-7 | Lazy-load memory content on click | Initial payload stays small (metadata only); full content fetched on demand |
| AD-8 | Store-agnostic route handlers | Same code path for SQLite and Postgres — routes use `Lore` facade, not store directly |

---

## 2. Component Architecture

### 2.1 Frontend Architecture

No framework. Modules are plain ES modules bundled by esbuild into a single IIFE.

```
src/lore/ui/
├── src/                          # Source (ES modules)
│   ├── index.js                  # Entry point — bootstraps app
│   ├── graph/
│   │   ├── renderer.js           # Canvas rendering loop (nodes, edges, labels)
│   │   ├── simulation.js         # D3-force simulation config + tick handler
│   │   ├── interaction.js        # Mouse/touch: click, drag, hover, zoom, pan
│   │   └── layout.js             # Cluster layout + force layout switching
│   ├── panels/
│   │   ├── detail.js             # Right panel — memory/entity detail view
│   │   ├── filters.js            # Left panel — filter controls
│   │   └── stats.js              # Stats dashboard panel
│   ├── components/
│   │   ├── search.js             # Search bar + results list
│   │   ├── timeline.js           # Bottom timeline scrubber
│   │   └── minimap.js            # Corner minimap (canvas-in-canvas)
│   ├── state.js                  # Central state object (no framework — plain object + events)
│   ├── api.js                    # Fetch wrapper for /v1/ui/* endpoints
│   ├── colors.js                 # Color palette constants from PRD Section 5
│   └── utils.js                  # Debounce, format dates, truncate text
├── dist/                         # Built output (checked into git)
│   ├── index.html                # Single HTML file with inlined CSS, <script> tag
│   ├── app.js                    # Bundled + minified JS
│   └── d3-force.min.js           # D3 force module (subset, not full D3)
├── static/
│   └── favicon.svg               # Brain emoji as SVG favicon
├── build.mjs                     # esbuild build script
└── package.json                  # Dev dependencies only (esbuild, d3-force)
```

**Why this structure:**
- `graph/` isolates the rendering pipeline — renderer, simulation, interaction are independent concerns
- `panels/` maps 1:1 to the PRD's UI regions (detail, filters, stats)
- `state.js` is a plain object with `EventTarget` for pub/sub — no Redux, no stores, just `state.on('filterChange', cb)`
- `dist/` is checked into git so `pip install lore-memory` works without npm

### 2.2 State Management

No framework state management. A single `AppState` object owns all mutable state and emits events when it changes.

```javascript
// state.js — simplified
class AppState extends EventTarget {
  constructor() {
    super();
    this.nodes = [];           // All graph nodes (from API)
    this.edges = [];           // All graph edges (from API)
    this.filteredNodeIds = new Set();  // IDs passing current filters
    this.selectedNodeId = null;
    this.hoveredNodeId = null;
    this.searchResults = [];
    this.filters = {
      projects: [],            // Multi-select
      memoryTypes: [],         // Checkboxes
      entityTypes: [],         // Checkboxes
      tiers: [],               // Checkboxes
      dateRange: [null, null], // [start, end]
      minImportance: 0.0,      // Slider
      searchQuery: '',         // Text
    };
    this.viewMode = 'force';   // 'force' | 'cluster-project' | 'cluster-type'
    this.stats = null;
    this.timelineBuckets = [];
  }

  setFilter(key, value) {
    this.filters[key] = value;
    this._recomputeFiltered();
    this.dispatchEvent(new CustomEvent('filterChange'));
  }

  selectNode(id) {
    this.selectedNodeId = id;
    this.dispatchEvent(new CustomEvent('selectionChange', { detail: { id } }));
  }

  _recomputeFiltered() {
    // Client-side filter: iterate nodes, apply all filter predicates, update filteredNodeIds
    // O(n) where n = node count — instant for <10K nodes
  }
}
```

**Event flow:**
1. User changes filter → `state.setFilter()` → recomputes `filteredNodeIds` → emits `filterChange`
2. `renderer.js` listens to `filterChange` → redraws nodes with opacity (filtered out = 10% opacity)
3. `filters.js` listens to `filterChange` → updates badge count
4. URL is updated via `history.replaceState()` with current filter state (bookmarkable)

### 2.3 Backend Architecture

New route module plugged into existing FastAPI server:

```
src/lore/server/routes/
├── ... (existing routes)
└── ui.py                  # New: /v1/ui/* endpoints

src/lore/server/
├── app.py                 # Modified: mount ui router + StaticFiles
└── ...
```

**Store-agnostic design:** The `lore ui` command instantiates a `Lore` object (which auto-detects SQLite vs Postgres) and passes it to the route handlers. This means:
- SQLite users: `lore ui` works out of the box, reads `~/.lore/lore.db`
- Postgres users: `lore ui --server http://localhost:8765` proxies to the existing server

---

## 3. Graph Visualization Library Decision

### D3-force + Canvas (Selected)

**D3 subset used** (not the full 520KB D3 library):

| D3 Module | Size (min+gz) | Purpose |
|-----------|---------------|---------|
| d3-force | ~12KB | Force simulation (forceLink, forceManyBody, forceCenter, forceCollide) |
| d3-quadtree | ~4KB | Spatial indexing for hit testing and collision |
| d3-zoom | ~8KB | Zoom/pan transform management |
| d3-scale | ~6KB | Linear scale for importance → node size mapping |
| d3-selection | ~8KB | Minimal DOM utilities (event binding) |
| **Total** | **~38KB** | Well under 300KB budget |

### Alternatives Evaluated

| Library | Size | WebGL | Fit | Rejection Reason |
|---------|------|-------|-----|-----------------|
| **Sigma.js** | 170KB | Yes | Good for large graphs | Overkill — D3-force sufficient for 5K target. Sigma's WebGL renderer adds complexity without clear benefit at our scale. Could swap in later if needed. |
| **Cytoscape.js** | 230KB | No | Good API | Too large for budget. SVG-based — performance wall at ~500 nodes. |
| **Vis.js** | 400KB+ | No | Easy setup | Way too large. Unmaintained. |
| **Pixi.js + custom** | 200KB | Yes | Max performance | Too low-level — we'd rebuild D3-force's simulation from scratch. |
| **@antv/G6** | 800KB+ | WebGL | Feature-rich | Enormous bundle, heavy framework dependency. |

### Why Not WebGL for V1?

D3-force + Canvas handles 5K nodes at 60fps. WebGL is warranted at 10K+ nodes. The PRD targets 1K-5K. If we hit scale issues:
- The data layer (state, API) is renderer-agnostic
- Swap `renderer.js` for a WebGL renderer (PixiJS or raw WebGL) without touching simulation or state

---

## 4. Backend API Design

### 4.1 Route Module: `src/lore/server/routes/ui.py`

All endpoints prefixed `/v1/ui/`. No authentication for localhost (matches NFR-5).

#### Endpoint Summary

| Method | Path | Purpose | Data Source |
|--------|------|---------|-------------|
| GET | `/v1/ui/graph` | Full graph (nodes + edges + stats) | Store + EntityCache + Relationships |
| GET | `/v1/ui/graph/clusters` | Nodes grouped by project/type | Store + groupby logic |
| GET | `/v1/ui/memory/{id}` | Full memory content + connections | Store.get() + entity mentions |
| GET | `/v1/ui/entity/{id}` | Full entity details + connections | Entity store + relationship store |
| POST | `/v1/ui/search` | Semantic/keyword search | Existing search pipeline |
| GET | `/v1/ui/stats` | Aggregate statistics | Store.count() + aggregations |
| GET | `/v1/ui/timeline` | Memory density over time | SQL GROUP BY date bucket |

#### 4.2 GET `/v1/ui/graph` — Primary Data Endpoint

This is the most critical endpoint. It assembles the complete visualization dataset.

**Implementation approach:**

```python
# src/lore/server/routes/ui.py (simplified)

from fastapi import APIRouter, Query
from lore import Lore
from lore.types import Entity, Relationship, EntityMention

router = APIRouter(prefix="/v1/ui", tags=["ui"])

@router.get("/graph")
async def get_graph(
    project: str | None = None,
    type: str | None = None,
    entity_type: str | None = None,
    tier: str | None = None,
    since: str | None = None,
    until: str | None = None,
    min_importance: float = 0.0,
    limit: int = 1000,
    include_orphans: bool = True,
):
    """Return graph data for visualization.

    Strategy:
    1. Fetch all entities (from EntityCache — fast, already in-memory)
    2. Fetch all relationships (filtered by validity)
    3. Fetch memory metadata (no content) with filters applied
    4. Fetch entity_mentions to create memory↔entity edges
    5. Assemble into {nodes, edges, stats} response
    """
    lore = get_lore_instance()  # Injected via app.state

    # Step 1: Entities → entity nodes
    entities = await lore.store.list_entities(
        entity_type=entity_type, limit=limit
    )

    # Step 2: Relationships → entity↔entity edges
    relationships = await lore.store.list_relationships(
        active_only=True
    )

    # Step 3: Memories → memory nodes (metadata only, no content)
    memories = await lore.store.list(
        project=project, type=type, tier=tier, limit=limit
    )
    # Apply date and importance filters in Python if store doesn't support them
    if since:
        memories = [m for m in memories if m.created_at >= since]
    if until:
        memories = [m for m in memories if m.created_at <= until]
    if min_importance > 0:
        memories = [m for m in memories if m.importance_score >= min_importance]

    # Step 4: Entity mentions → memory↔entity edges
    memory_ids = {m.id for m in memories}
    entity_ids = {e.id for e in entities}
    mentions = await lore.store.list_entity_mentions(
        memory_ids=list(memory_ids)
    )

    # Step 5: Assemble response
    nodes = []
    for m in memories:
        nodes.append({
            "id": m.id,
            "kind": "memory",
            "label": m.content[:60] if m.content else "",
            "type": m.type,
            "tier": m.tier,
            "project": m.project,
            "importance": m.importance_score,
            "confidence": m.confidence,
            "tags": m.tags,
            "created_at": m.created_at,
            "upvotes": m.upvotes,
            "downvotes": m.downvotes,
            "access_count": m.access_count,
        })
    for e in entities:
        nodes.append({
            "id": e.id,
            "kind": "entity",
            "label": e.name,
            "type": e.entity_type,
            "mention_count": e.mention_count,
            "aliases": e.aliases,
            "first_seen_at": e.first_seen_at,
            "last_seen_at": e.last_seen_at,
        })

    edges = []
    for r in relationships:
        if r.source_entity_id in entity_ids and r.target_entity_id in entity_ids:
            edges.append({
                "source": r.source_entity_id,
                "target": r.target_entity_id,
                "rel_type": r.rel_type,
                "weight": r.weight,
                "label": r.rel_type,
            })
    for mention in mentions:
        if mention.entity_id in entity_ids and mention.memory_id in memory_ids:
            edges.append({
                "source": mention.memory_id,
                "target": mention.entity_id,
                "rel_type": "mentions",
                "weight": mention.confidence,
                "label": "mentions",
            })

    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "total_memories": await lore.store.count(),
            "total_entities": len(entities),
            "total_relationships": len(relationships),
            "filtered_nodes": len(nodes),
            "filtered_edges": len(edges),
        },
    }
```

**Key implementation notes:**
- Leverages existing `Store.list()`, `Store.list_entities()`, `Store.list_relationships()` methods
- No new database tables needed — all data already exists
- Entity mentions are the bridge between memory nodes and entity nodes
- Memory `content` deliberately excluded from graph payload (lazy-loaded on click via `/v1/ui/memory/{id}`)

#### 4.3 Store Interface Extensions

The existing `Store` ABC needs a few methods that may not exist yet:

```python
# Methods needed (verify existence, add to Store ABC if missing):
class Store(ABC):
    # Existing:
    async def list(project, type, limit) -> List[Memory]
    async def get(memory_id) -> Optional[Memory]
    async def count(project, type) -> int

    # May need to add:
    async def list_entities(entity_type=None, limit=None) -> List[Entity]
    async def list_relationships(active_only=True) -> List[Relationship]
    async def list_entity_mentions(memory_ids=None) -> List[EntityMention]
```

The graph module already has `EntityCache.get_all()` which returns all entities. For relationships and entity mentions, the store layer needs query methods. The SQLite store already has these tables (created during graph feature work) — we just need the Python query methods.

#### 4.4 Response Size Analysis

For a user with 1000 memories and 200 entities:

| Field | Est. Size per Node | Total |
|-------|--------------------|-------|
| Memory node (no content) | ~200 bytes JSON | 200KB |
| Entity node | ~150 bytes JSON | 30KB |
| Edge (relationship) | ~100 bytes JSON | ~50KB (500 edges) |
| Edge (mention) | ~80 bytes JSON | ~120KB (1500 mentions) |
| Stats | ~200 bytes | <1KB |
| **Total** | | **~400KB** |

This is well within acceptable limits. Even at 5000 memories, we're looking at ~2MB — one HTTP response, no pagination needed. Gzip compression reduces this by ~70% to ~600KB over the wire.

**Pagination trigger:** Only if `total_memories > limit` (default 1000). The response includes `stats.total_memories` so the frontend knows if data was truncated and can show a "Load more" option or suggest increasing `min_importance`.

---

## 5. Data Flow

### 5.1 Initial Load Sequence

```
Browser                     Server                      Store
  │                           │                           │
  │  GET /v1/ui/graph         │                           │
  │  ?limit=1000              │                           │
  │ ─────────────────────────▶│                           │
  │                           │  list_entities()          │
  │                           │ ─────────────────────────▶│
  │                           │◀─────────────────────────│
  │                           │  list_relationships()     │
  │                           │ ─────────────────────────▶│
  │                           │◀─────────────────────────│
  │                           │  list(limit=1000)         │
  │                           │ ─────────────────────────▶│
  │                           │◀─────────────────────────│
  │                           │  list_entity_mentions()   │
  │                           │ ─────────────────────────▶│
  │                           │◀─────────────────────────│
  │                           │                           │
  │  { nodes, edges, stats }  │                           │
  │◀─────────────────────────│                           │
  │                           │                           │
  │  [Initialize D3 force]    │                           │
  │  [Start Canvas render]    │                           │
  │                           │                           │
  │  GET /v1/ui/stats         │                           │
  │ ─────────────────────────▶│  (parallel)               │
  │                           │                           │
  │  GET /v1/ui/timeline      │                           │
  │ ─────────────────────────▶│  (parallel)               │
```

### 5.2 Node Click → Detail Panel

```
Browser                     Server
  │                           │
  │  [User clicks memory node]│
  │                           │
  │  GET /v1/ui/memory/{id}   │
  │ ─────────────────────────▶│
  │                           │  store.get(id)
  │  { full content, ...}     │  + entity mentions
  │◀─────────────────────────│  + related memories
  │                           │
  │  [Render detail panel]    │
  │  [Render markdown content]│
  │  [Cache in Map]           │
```

### 5.3 Filter Flow (Client-Side)

```
User adjusts filter slider
  │
  ▼
state.setFilter('minImportance', 0.5)
  │
  ▼
state._recomputeFiltered()          ◀── O(n) scan, <1ms for 5K nodes
  │
  ├──▶ filteredNodeIds updated
  │
  ▼
dispatchEvent('filterChange')
  │
  ├──▶ renderer.js: redraw with opacity
  ├──▶ filters.js: update badge count
  └──▶ URL: history.replaceState with filter params
```

**No server round-trip.** All filtering happens client-side against the already-loaded dataset. This gives sub-frame filter response time.

### 5.4 Search Flow

```
User types "redis caching"
  │
  ▼ (debounced 300ms)
api.search({ query: "redis caching", mode: "semantic" })
  │
  ▼
POST /v1/ui/search
  │
  ▼ (server: uses existing search pipeline + embeddings)
{ results: [{ id, kind, label, score }] }
  │
  ▼
state.searchResults = results
  │
  ├──▶ renderer.js: highlight matching nodes (glow), dim others
  ├──▶ search.js: show results list in panel
  └──▶ [click result] → graph.centerOnNode(id) + state.selectNode(id)
```

---

## 6. Interactive Features — Implementation Details

### 6.1 Canvas Rendering Pipeline

```javascript
// renderer.js — render loop
function render(ctx, state, transform) {
  ctx.save();
  ctx.clearRect(0, 0, width, height);

  // Apply zoom/pan transform
  ctx.translate(transform.x, transform.y);
  ctx.scale(transform.k, transform.k);

  // 1. Draw edges (below nodes)
  for (const edge of state.edges) {
    const visible = state.filteredNodeIds.has(edge.source.id)
                 && state.filteredNodeIds.has(edge.target.id);
    ctx.globalAlpha = visible ? 0.4 : 0.04;
    drawEdge(ctx, edge);
  }

  // 2. Draw nodes
  for (const node of state.nodes) {
    const visible = state.filteredNodeIds.has(node.id);
    ctx.globalAlpha = visible ? opacityFromConfidence(node) : 0.1;

    if (node.kind === 'memory') {
      drawCircle(ctx, node);        // Circle, sized by importance
    } else {
      drawHexagon(ctx, node);       // Hexagon, sized by mention_count
    }

    // Label (only if zoom level > threshold)
    if (transform.k > 0.8) {
      drawLabel(ctx, node);
    }
  }

  // 3. Draw selection highlight
  if (state.selectedNodeId) {
    drawSelectionGlow(ctx, state.selectedNode);
  }

  // 4. Draw search highlights
  for (const result of state.searchResults) {
    drawSearchPulse(ctx, result);
  }

  ctx.restore();
}
```

### 6.2 Hit Testing (Click/Hover on Canvas)

Canvas doesn't have DOM elements. Use D3 quadtree for O(log n) spatial lookup:

```javascript
// interaction.js
const quadtree = d3.quadtree()
  .x(d => d.x)
  .y(d => d.y);

// Rebuild on simulation tick (nodes move)
simulation.on('tick', () => {
  quadtree.addAll(state.nodes);
  render(ctx, state, transform);
});

canvas.addEventListener('mousemove', (e) => {
  const [mx, my] = screenToGraph(e.clientX, e.clientY, transform);
  const node = quadtree.find(mx, my, 20);  // 20px search radius
  state.hoverNode(node?.id || null);
});

canvas.addEventListener('click', (e) => {
  const [mx, my] = screenToGraph(e.clientX, e.clientY, transform);
  const node = quadtree.find(mx, my, 20);
  if (node) state.selectNode(node.id);
});
```

### 6.3 Drag Nodes

```javascript
// interaction.js — D3 drag behavior on canvas
d3.select(canvas).call(
  d3.drag()
    .subject((event) => {
      const [mx, my] = screenToGraph(event.x, event.y, transform);
      return quadtree.find(mx, my, 20);
    })
    .on('start', (event) => {
      simulation.alphaTarget(0.3).restart();
      event.subject.fx = event.subject.x;
      event.subject.fy = event.subject.y;
    })
    .on('drag', (event) => {
      event.subject.fx = event.x;
      event.subject.fy = event.y;
    })
    .on('end', (event) => {
      simulation.alphaTarget(0);
      // Keep pinned — user explicitly positioned this node
      // event.subject.fx = null; // Uncomment to unpin on release
    })
);
```

### 6.4 Zoom/Pan

```javascript
// interaction.js — D3 zoom on canvas
const zoom = d3.zoom()
  .scaleExtent([0.1, 8])
  .on('zoom', (event) => {
    transform = event.transform;
    render(ctx, state, transform);
  });

d3.select(canvas).call(zoom);

// Programmatic: center on node
function centerOnNode(nodeId) {
  const node = state.nodes.find(n => n.id === nodeId);
  if (!node) return;
  d3.select(canvas)
    .transition()
    .duration(500)
    .call(zoom.transform,
      d3.zoomIdentity
        .translate(width / 2, height / 2)
        .scale(2)
        .translate(-node.x, -node.y)
    );
}
```

### 6.5 Minimap

A second, smaller canvas in the bottom-left corner:

```javascript
// minimap.js
const minimapCanvas = document.getElementById('minimap');
const mCtx = minimapCanvas.getContext('2d');
const MINIMAP_SIZE = 150;

function renderMinimap(state, viewTransform) {
  mCtx.clearRect(0, 0, MINIMAP_SIZE, MINIMAP_SIZE);

  // Compute bounds of all nodes
  const bounds = computeBounds(state.nodes);
  const scale = MINIMAP_SIZE / Math.max(bounds.width, bounds.height);

  // Draw all nodes as dots
  for (const node of state.nodes) {
    mCtx.fillStyle = getNodeColor(node);
    mCtx.globalAlpha = state.filteredNodeIds.has(node.id) ? 0.8 : 0.1;
    const x = (node.x - bounds.minX) * scale;
    const y = (node.y - bounds.minY) * scale;
    mCtx.fillRect(x, y, 2, 2);
  }

  // Draw viewport rectangle
  const vp = screenToGraphRect(viewTransform, width, height);
  mCtx.strokeStyle = '#ffffff';
  mCtx.globalAlpha = 0.5;
  mCtx.strokeRect(
    (vp.x - bounds.minX) * scale,
    (vp.y - bounds.minY) * scale,
    vp.width * scale,
    vp.height * scale
  );
}
```

---

## 7. Performance Strategy

### 7.1 Rendering Performance Targets

| Node Count | Technique | Expected FPS |
|-----------|-----------|-------------|
| < 500 | Canvas 2D, all labels visible | 60fps |
| 500–2000 | Canvas 2D, labels only on zoom | 60fps |
| 2000–5000 | Canvas 2D, skip edge rendering when zoomed out, LOD | 30-60fps |
| 5000+ | Show warning, importance-based pruning to 2000 nodes | 60fps |

### 7.2 Level of Detail (LOD)

```javascript
function render(ctx, state, transform) {
  const zoom = transform.k;

  // LOD thresholds
  const showLabels = zoom > 0.8;
  const showEdges = zoom > 0.3 || state.nodes.length < 1000;
  const showArrowheads = zoom > 1.5;
  const showBorderStyle = zoom > 1.0;  // Tier-based border

  // Skip edges when zoomed out on large graphs
  if (showEdges) {
    renderEdges(ctx, state, { showArrowheads });
  }

  renderNodes(ctx, state, { showLabels, showBorderStyle });
}
```

### 7.3 Simulation Performance

```javascript
// simulation.js
const simulation = d3.forceSimulation(nodes)
  .force('link', d3.forceLink(edges).id(d => d.id)
    .distance(d => 60 / (d.weight || 0.5))  // Stronger = closer
    .strength(d => Math.min(d.weight, 1.0))
  )
  .force('charge', d3.forceManyBody()
    .strength(-30)                // Repulsion
    .distanceMax(300)             // Limit calculation radius (performance)
  )
  .force('center', d3.forceCenter(width / 2, height / 2)
    .strength(0.05)               // Gentle centering
  )
  .force('collide', d3.forceCollide()
    .radius(d => nodeRadius(d) + 2)  // Prevent overlap
    .iterations(1)                    // Single iteration per tick (fast)
  )
  .alphaDecay(0.02)               // Cool down over ~150 ticks
  .velocityDecay(0.4);            // Damping

// Stop simulation when cooled (don't burn CPU)
simulation.on('end', () => {
  // Final render, no more ticks
});
```

### 7.4 Network Performance

| Optimization | Implementation |
|-------------|---------------|
| Gzip compression | FastAPI GZipMiddleware (already available) |
| Memory content lazy-load | Only fetched on node click, cached in `Map<id, content>` |
| Parallel initial requests | `Promise.all([fetchGraph(), fetchStats(), fetchTimeline()])` |
| Debounced search | 300ms debounce on search input |
| No duplicate fetches | In-flight request deduplication in `api.js` |

### 7.5 Memory Management

```javascript
// api.js — content cache with LRU-like eviction
const contentCache = new Map();
const MAX_CACHE = 200;

async function fetchMemoryDetail(id) {
  if (contentCache.has(id)) return contentCache.get(id);

  const data = await fetch(`/v1/ui/memory/${id}`).then(r => r.json());

  if (contentCache.size >= MAX_CACHE) {
    // Delete oldest entry (first inserted)
    const firstKey = contentCache.keys().next().value;
    contentCache.delete(firstKey);
  }
  contentCache.set(id, data);
  return data;
}
```

---

## 8. Build & Serve Strategy

### 8.1 Build Pipeline (Development Only)

```javascript
// build.mjs
import * as esbuild from 'esbuild';

await esbuild.build({
  entryPoints: ['src/index.js'],
  bundle: true,
  minify: true,
  outfile: 'dist/app.js',
  format: 'iife',
  target: ['es2020'],
  external: [],  // Bundle everything
});
```

**Build command:** `node build.mjs` (runs in <100ms)

**D3 modules:** Installed as npm dev dependencies, bundled by esbuild into `app.js`. No CDN, no external requests. Only the subset we need (d3-force, d3-quadtree, d3-zoom, d3-scale, d3-selection) — not the full D3 library.

**`dist/` is checked into git.** This means:
- `pip install lore-memory` → UI works immediately, no npm step
- CI validates that `dist/` is up to date (run build, check git diff)
- Contributors run `npm run build` after changing `src/` files

### 8.2 index.html

```html
<!-- dist/index.html -->
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Lore — Knowledge Graph</title>
  <link rel="icon" href="data:image/svg+xml,<svg xmlns='...'>🧠</svg>">
  <style>
    /* ~200 lines of CSS — dark theme, layout, panels, animations */
    /* Inlined to avoid extra HTTP request */
    :root {
      --bg: #0a0a0f;
      --panel-bg: #141420;
      --text: #e2e8f0;
      --text-muted: #94a3b8;
      --border: #1e293b;
      --accent: #6b8afd;
    }
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { background: var(--bg); color: var(--text); font-family: -apple-system, ...; }
    /* ... layout classes ... */
  </style>
</head>
<body>
  <header id="header"><!-- Search bar, controls --></header>
  <aside id="filters"><!-- Filter panel --></aside>
  <main id="graph-container">
    <canvas id="graph-canvas"></canvas>
    <canvas id="minimap" width="150" height="150"></canvas>
  </main>
  <aside id="detail-panel" class="hidden"><!-- Detail panel --></aside>
  <footer id="timeline"><!-- Timeline scrubber --></footer>

  <script src="app.js"></script>
</body>
</html>
```

### 8.3 CLI Command: `lore ui`

```python
# cli.py — new command

@app.command()
def ui(
    port: int = typer.Option(8766, help="Port to serve on"),
    host: str = typer.Option("127.0.0.1", help="Host to bind to"),
    no_open: bool = typer.Option(False, "--no-open", help="Don't open browser"),
):
    """Launch the knowledge graph visualization in your browser."""
    import uvicorn
    import webbrowser
    from pathlib import Path

    # Verify UI assets exist
    ui_dist = Path(__file__).parent / "ui" / "dist"
    if not (ui_dist / "index.html").exists():
        typer.echo("Error: UI assets not found. Reinstall lore-memory.", err=True)
        raise typer.Exit(1)

    # Create a dedicated FastAPI app for the UI
    from lore.server.ui_app import create_ui_app
    app = create_ui_app(ui_dist)

    if not no_open:
        # Open browser after short delay (server needs to start)
        import threading
        def open_browser():
            import time
            time.sleep(0.5)
            webbrowser.open(f"http://{host}:{port}")
        threading.Thread(target=open_browser, daemon=True).start()

    typer.echo(f"Lore UI available at http://{host}:{port}")
    typer.echo("Press Ctrl+C to stop")

    uvicorn.run(app, host=host, port=port, log_level="warning")
```

### 8.4 UI App Factory

```python
# src/lore/server/ui_app.py

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from lore import Lore
from lore.server.routes.ui import router as ui_router

def create_ui_app(static_dir: Path) -> FastAPI:
    """Create a lightweight FastAPI app for the graph UI.

    Separate from the main Lore Cloud server — this app:
    1. Serves static files (HTML/JS/CSS)
    2. Exposes /v1/ui/* API endpoints
    3. Reads from local store (SQLite or Postgres via Lore facade)
    4. Binds to localhost only by default
    """
    lore = Lore()  # Auto-detects SQLite/Postgres from config

    app = FastAPI(title="Lore Graph UI", version="1.0")
    app.add_middleware(GZipMiddleware, minimum_size=500)

    # Store lore instance for route handlers
    app.state.lore = lore

    # API routes
    app.include_router(ui_router)

    # Static files (serve index.html at root)
    app.mount("/", StaticFiles(directory=str(static_dir), html=True))

    return app
```

**Key design choice:** `create_ui_app()` is a separate FastAPI application from the existing Lore Cloud server (`app.py`). This means:
- `lore ui` works standalone — no need to run `lore serve` first
- The UI app reads directly from the local store
- No dependency on Postgres server for SQLite users
- The existing server can optionally mount the UI routes too (for cloud deployments)

---

## 9. Responsive Design

### 9.1 Breakpoints

| Breakpoint | Width | Layout |
|-----------|-------|--------|
| Desktop | >1024px | Three-column: filters (220px) + graph + detail (320px) |
| Tablet | 768–1024px | Graph fullscreen. Filters = icon bar (48px). Detail = overlay |
| Mobile | <768px | Graph fullscreen. Filters + detail = bottom sheets |

### 9.2 Implementation

```css
/* Desktop (default) */
body {
  display: grid;
  grid-template-columns: 220px 1fr 0;  /* Detail column is 0 until opened */
  grid-template-rows: 48px 1fr 48px;
  grid-template-areas:
    "header header header"
    "filters graph detail"
    "timeline timeline timeline";
  height: 100vh;
}

body.detail-open {
  grid-template-columns: 220px 1fr 320px;
}

/* Tablet */
@media (max-width: 1024px) {
  body {
    grid-template-columns: 48px 1fr;
    grid-template-areas:
      "header header"
      "filters graph"
      "timeline timeline";
  }
  #filters { /* Icon-only sidebar */ }
  #detail-panel {
    position: fixed;
    right: 0; top: 48px; bottom: 48px;
    width: 320px;
    z-index: 10;
    box-shadow: -4px 0 20px rgba(0,0,0,0.5);
  }
}

/* Mobile */
@media (max-width: 768px) {
  body {
    grid-template-columns: 1fr;
    grid-template-rows: 48px 1fr;
    grid-template-areas:
      "header"
      "graph";
  }
  #filters, #detail-panel {
    position: fixed;
    bottom: 0; left: 0; right: 0;
    max-height: 60vh;
    border-radius: 16px 16px 0 0;
    transform: translateY(100%);
    transition: transform 250ms ease-out;
  }
  #filters.open, #detail-panel.open {
    transform: translateY(0);
  }
  #timeline { display: none; }  /* Hidden on mobile — too small */
}
```

### 9.3 Touch Support

```javascript
// interaction.js — touch handling
// D3-zoom already handles touch zoom/pan (pinch + two-finger drag)
// Add: tap = click, long-press = right-click context menu

canvas.addEventListener('touchstart', (e) => {
  if (e.touches.length === 1) {
    longPressTimer = setTimeout(() => {
      // Long press → context menu
      showContextMenu(e.touches[0]);
    }, 500);
  }
});

canvas.addEventListener('touchend', (e) => {
  clearTimeout(longPressTimer);
  // Single tap → select node (handled by click event)
});
```

---

## 10. Testing Strategy

### 10.1 Backend Tests

| Layer | Test Type | Framework | Location |
|-------|----------|-----------|----------|
| API endpoints | Integration | pytest + httpx (TestClient) | `tests/server/test_ui_routes.py` |
| Graph assembly | Unit | pytest | `tests/server/test_ui_graph.py` |
| Store methods | Unit | pytest | `tests/store/test_ui_queries.py` |
| CLI command | Integration | pytest + typer.testing.CliRunner | `tests/test_cli_ui.py` |

**Backend test examples:**

```python
# tests/server/test_ui_routes.py

async def test_graph_returns_nodes_and_edges(client, seeded_db):
    """GET /v1/ui/graph returns correct structure."""
    resp = await client.get("/v1/ui/graph")
    assert resp.status_code == 200
    data = resp.json()
    assert "nodes" in data
    assert "edges" in data
    assert "stats" in data
    # Verify node structure
    node = data["nodes"][0]
    assert "id" in node
    assert "kind" in node
    assert node["kind"] in ("memory", "entity")

async def test_graph_filters_by_project(client, seeded_db):
    resp = await client.get("/v1/ui/graph?project=auth-service")
    data = resp.json()
    for node in data["nodes"]:
        if node["kind"] == "memory":
            assert node["project"] == "auth-service"

async def test_memory_detail_returns_content(client, seeded_db):
    resp = await client.get(f"/v1/ui/memory/{KNOWN_MEMORY_ID}")
    data = resp.json()
    assert "content" in data
    assert "connected_entities" in data

async def test_graph_importance_filter(client, seeded_db):
    resp = await client.get("/v1/ui/graph?min_importance=0.8")
    data = resp.json()
    for node in data["nodes"]:
        if node["kind"] == "memory":
            assert node["importance"] >= 0.8

async def test_search_returns_scored_results(client, seeded_db):
    resp = await client.post("/v1/ui/search", json={
        "query": "redis", "mode": "keyword", "limit": 10
    })
    data = resp.json()
    assert "results" in data
    assert all("score" in r for r in data["results"])
```

### 10.2 Frontend Tests

| Layer | Test Type | Tool | Notes |
|-------|----------|------|-------|
| State management | Unit | Vitest (runs in Node) | Test filter logic, event dispatch |
| API client | Unit | Vitest + MSW (mock server) | Test fetch, caching, error handling |
| Canvas rendering | Snapshot | Playwright | Screenshot comparison at key states |
| Interactions | E2E | Playwright | Click, drag, zoom, search |
| Performance | Benchmark | Playwright + custom timing | Render time for 100/1000/5000 nodes |

**Frontend test structure:**

```
src/lore/ui/
├── tests/
│   ├── unit/
│   │   ├── state.test.js       # Filter logic, event dispatch
│   │   ├── api.test.js         # Fetch wrapper, caching
│   │   └── colors.test.js      # Color mapping correctness
│   ├── e2e/
│   │   ├── graph.spec.js       # Load graph, click node, verify panel
│   │   ├── filters.spec.js     # Apply filters, verify node opacity
│   │   ├── search.spec.js      # Search, verify highlights
│   │   └── responsive.spec.js  # Test at 3 viewport sizes
│   └── perf/
│       └── render.bench.js     # Measure render time at scale
```

**E2E test example (Playwright):**

```javascript
// tests/e2e/graph.spec.js
test('clicking a node opens detail panel', async ({ page }) => {
  await page.goto('http://localhost:8766');
  await page.waitForSelector('#graph-canvas');

  // Wait for graph to render (simulation to cool)
  await page.waitForTimeout(2000);

  // Click center of canvas (likely has a node)
  const canvas = page.locator('#graph-canvas');
  await canvas.click({ position: { x: 400, y: 300 } });

  // Detail panel should appear
  await expect(page.locator('#detail-panel')).toBeVisible();
});
```

### 10.3 Performance Tests

```javascript
// tests/perf/render.bench.js
import { generateMockGraph } from '../helpers';

for (const count of [100, 500, 1000, 2000, 5000]) {
  test(`render ${count} nodes under budget`, async ({ page }) => {
    // Inject mock data via page.evaluate
    const graph = generateMockGraph(count);
    const startTime = await page.evaluate((data) => {
      window.__testData = data;
      const t0 = performance.now();
      window.app.loadGraph(data);
      return t0;
    }, graph);

    // Wait for simulation to stabilize
    await page.waitForFunction(() => window.app.simulationAlpha() < 0.01);
    const elapsed = await page.evaluate(() => performance.now() - window.__t0);

    // PRD targets
    if (count <= 100) expect(elapsed).toBeLessThan(500);
    if (count <= 1000) expect(elapsed).toBeLessThan(2000);
    if (count <= 5000) expect(elapsed).toBeLessThan(5000);
  });
}
```

### 10.4 CI Integration

```yaml
# In existing CI pipeline
ui-tests:
  steps:
    - pip install -e ".[server]"
    - cd src/lore/ui && npm ci && npm run build
    - diff --exit-code dist/  # Verify dist/ is up to date
    - npm test                # Unit tests (Vitest)
    - pytest tests/server/test_ui_routes.py  # Backend API tests
    - npx playwright test     # E2E tests (starts server automatically)
```

### 10.5 Bundle Size Check

```yaml
# CI step
bundle-size:
  steps:
    - cd src/lore/ui
    - node build.mjs
    - |
      TOTAL=$(wc -c < dist/app.js)
      HTML=$(wc -c < dist/index.html)
      SUM=$((TOTAL + HTML))
      if [ "$SUM" -gt 512000 ]; then
        echo "FAIL: Bundle size ${SUM} exceeds 500KB"
        exit 1
      fi
```

---

## 11. Security Considerations

| Concern | Mitigation |
|---------|-----------|
| Localhost binding | Default `host=127.0.0.1`. CLI warns if `--host 0.0.0.0` used without auth |
| XSS in memory content | Memory content rendered as text (not innerHTML). Markdown rendered via a minimal, safe renderer (no raw HTML pass-through) |
| No external requests | All assets bundled. CSP header: `default-src 'self'; script-src 'self'` |
| API key for remote | If `--host 0.0.0.0`, require API key via header (same auth as existing server) |
| CORS | Not needed (same-origin, served from same server) |

---

## 12. File Inventory (New & Modified Files)

### New Files

| File | Purpose | Est. Lines |
|------|---------|-----------|
| `src/lore/ui/src/index.js` | Entry point, bootstrap | 50 |
| `src/lore/ui/src/state.js` | AppState + EventTarget | 120 |
| `src/lore/ui/src/api.js` | Fetch wrapper + cache | 80 |
| `src/lore/ui/src/colors.js` | Color constants | 60 |
| `src/lore/ui/src/utils.js` | Debounce, format, truncate | 40 |
| `src/lore/ui/src/graph/renderer.js` | Canvas render loop | 200 |
| `src/lore/ui/src/graph/simulation.js` | D3-force config | 80 |
| `src/lore/ui/src/graph/interaction.js` | Mouse/touch handling | 150 |
| `src/lore/ui/src/graph/layout.js` | Force/cluster switching | 80 |
| `src/lore/ui/src/panels/detail.js` | Detail panel | 120 |
| `src/lore/ui/src/panels/filters.js` | Filter sidebar | 150 |
| `src/lore/ui/src/panels/stats.js` | Stats dashboard | 80 |
| `src/lore/ui/src/components/search.js` | Search bar + results | 100 |
| `src/lore/ui/src/components/timeline.js` | Timeline scrubber | 100 |
| `src/lore/ui/src/components/minimap.js` | Corner minimap | 60 |
| `src/lore/ui/dist/index.html` | Built HTML | 80 |
| `src/lore/ui/dist/app.js` | Built JS bundle | (minified) |
| `src/lore/ui/build.mjs` | esbuild script | 20 |
| `src/lore/ui/package.json` | Dev deps (esbuild, d3) | 15 |
| `src/lore/server/routes/ui.py` | API endpoints | 300 |
| `src/lore/server/ui_app.py` | UI FastAPI app factory | 40 |
| `tests/server/test_ui_routes.py` | Backend API tests | 200 |
| `tests/test_cli_ui.py` | CLI integration tests | 80 |
| `src/lore/ui/tests/unit/*.test.js` | Frontend unit tests | 200 |
| `src/lore/ui/tests/e2e/*.spec.js` | Frontend E2E tests | 200 |

**Total new code: ~2,600 lines** (excluding minified bundle and tests)

### Modified Files

| File | Change |
|------|--------|
| `src/lore/cli.py` | Add `lore ui` command (~30 lines) |
| `src/lore/server/app.py` | Optionally mount UI router (~5 lines) |
| `src/lore/store/base.py` | Add `list_entities()`, `list_relationships()`, `list_entity_mentions()` if missing |
| `src/lore/store/sqlite.py` | Implement above methods |
| `pyproject.toml` | No new Python deps needed (FastAPI, uvicorn already in `[server]` extra) |

---

## 13. Implementation Phases (Mapped to PRD)

### Phase 1: Foundation (MVP) — Stories S1-S6

**Goal:** `lore ui` → browser opens → force-directed graph renders → click a node → see details.

| Step | Work | Files |
|------|------|-------|
| 1 | Backend: `/v1/ui/graph` + `/v1/ui/memory/{id}` + `/v1/ui/entity/{id}` endpoints | `routes/ui.py`, store extensions |
| 2 | Backend: `create_ui_app()` + `lore ui` CLI command | `ui_app.py`, `cli.py` |
| 3 | Frontend: D3 force simulation + Canvas renderer | `simulation.js`, `renderer.js` |
| 4 | Frontend: Zoom, pan, drag, click → detail panel | `interaction.js`, `detail.js` |
| 5 | Frontend: Node visual encoding (shape, color, size) | `renderer.js`, `colors.js` |
| 6 | Build: esbuild config, dist checked in | `build.mjs`, `package.json` |

### Phase 2: Interactivity — Stories S7-S12

| Step | Work | Files |
|------|------|-------|
| 7 | Backend: `/v1/ui/search` endpoint | `routes/ui.py` |
| 8 | Frontend: Filter sidebar (project, type, tier, importance) | `filters.js`, `state.js` |
| 9 | Frontend: Search bar with debounce + highlight | `search.js` |
| 10 | Frontend: URL state sync (bookmarkable filters) | `state.js`, `index.js` |
| 11 | Frontend: Hover tooltips, edge labels | `interaction.js`, `renderer.js` |
| 12 | Frontend: Context menu (right-click) | `interaction.js` |

### Phase 3: Polish — Stories S13-S18

| Step | Work | Files |
|------|------|-------|
| 13 | Backend: `/v1/ui/graph/clusters`, `/v1/ui/stats`, `/v1/ui/timeline` | `routes/ui.py` |
| 14 | Frontend: Cluster view with hull boundaries | `layout.js` |
| 15 | Frontend: Timeline scrubber | `timeline.js` |
| 16 | Frontend: Stats dashboard | `stats.js` |
| 17 | Frontend: Minimap | `minimap.js` |
| 18 | Frontend: Responsive layout + touch + keyboard nav | CSS, `interaction.js` |

---

## 14. Open Architecture Questions (Resolved)

| # | Question | Resolution |
|---|----------|-----------|
| 1 | Separate server or mount on existing? | **Separate.** `create_ui_app()` is standalone. Can optionally mount on cloud server later. Simpler for SQLite users. |
| 2 | How to handle SQLite vs Postgres? | **Lore facade.** `Lore()` auto-detects store type. Route handlers use the facade, never the store directly. |
| 3 | Markdown rendering in detail panel? | **Minimal parser.** Use a ~2KB inline markdown renderer (bold, italic, code, links, lists). No full CommonMark — avoids 30KB+ dependency. |
| 4 | How to bundle D3? | **esbuild tree-shakes.** Import only `d3-force`, `d3-quadtree`, `d3-zoom`, `d3-scale`, `d3-selection`. esbuild bundles only what's imported. Result: ~38KB min+gz. |
| 5 | Graph layout persistence? | **localStorage.** Save `{nodeId: {fx, fy}}` map. Restore on reload. No server persistence. |
| 6 | How does double-click expand work? | **`GraphTraverser.traverse()`** from clicked entity. New endpoint: `GET /v1/ui/graph/expand/{entity_id}?depth=1`. Returns adjacent nodes not already loaded. Frontend merges into existing simulation. |

---

## 15. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Canvas rendering performance at 5K nodes | Low | High | LOD system, importance-based pruning to 2K by default |
| D3-force simulation CPU usage on mobile | Medium | Medium | Lower iteration count on mobile, `alphaDecay(0.05)` for faster cooldown |
| Bundle size creeps over 500KB | Low | Low | CI check on every PR, alert at 400KB |
| SQLite concurrent access from CLI + UI server | Medium | Medium | SQLite WAL mode (already enabled), read-only queries in UI |
| Users expect React/Angular developer experience | Low | Low | Vanilla JS is intentional — document in CONTRIBUTING.md |
| Accessibility gaps in canvas-based UI | Medium | High | ARIA live regions for selection, keyboard tab order for panels, screenreader-friendly detail panel |

---

## Appendix A: D3 Module Dependency Graph

```
app.js (our code)
  └── d3-force (12KB)
       ├── d3-dispatch (2KB)
       ├── d3-quadtree (4KB)   ← also used directly for hit testing
       └── d3-timer (2KB)
  └── d3-zoom (8KB)
       ├── d3-dispatch (shared)
       ├── d3-selection (8KB)
       │    └── d3-dispatch (shared)
       ├── d3-transition (6KB)
       │    └── d3-selection (shared)
       └── d3-interpolate (4KB)
  └── d3-scale (6KB)
       └── d3-interpolate (shared)

Total unique: ~38KB minified + gzipped
```

## Appendix B: Color Palette Reference (From PRD)

Codified in `colors.js`:

```javascript
export const MEMORY_COLORS = {
  general: '#6b8afd', code: '#4ade80', lesson: '#fbbf24',
  fact: '#22d3ee', convention: '#a78bfa', preference: '#f472b6',
  debug: '#fb7185', pattern: '#2dd4bf', note: '#94a3b8',
};

export const ENTITY_COLORS = {
  person: '#fcd34d', tool: '#60a5fa', project: '#34d399',
  concept: '#c084fc', organization: '#fb923c', platform: '#818cf8',
  language: '#67e8f9', framework: '#fda4af', service: '#6ee7b7',
  other: '#9ca3af',
};

export const BG = '#0a0a0f';
export const PANEL_BG = '#141420';
```
