# E1: Graph Visualization (Web UI) — Product Requirements Document

**Version:** 1.0
**Epic:** E1 — Graph Visualization
**Target Release:** v0.11.0
**Date:** March 14, 2026
**Author:** PM (BMAD)
**Status:** Draft

---

## 1. Overview & Problem Statement

### The Marketing Problem

Obsidian's Graph View is their single most effective marketing asset. One screenshot — a glowing, interconnected constellation of knowledge — sells the product before a user reads a single word. Lore has richer data than Obsidian (entities, typed relationships, importance scores, memory tiers, temporal decay) but **zero visual layer**. We're selling an invisible product.

Today, a user runs `lore stats` and sees `total: 847`. That's a number, not a story. They have no way to see that their "auth-service" entity connects to 14 memories across 3 projects, or that their knowledge of Redis clusters has decayed while their Python expertise graph keeps growing.

### The User Problem

Power users of Lore accumulate hundreds of memories but lack tools to:
- **Audit** what their AI brain actually contains
- **Discover** unexpected connections between concepts
- **Prune** stale or low-quality memories
- **Trust** that the system is working correctly
- **Debug** why certain memories surface (or don't) during recall

Without visibility, Lore is a black box. Users can't build trust in a system they can't see.

### The Solution

A lightweight, self-hosted web UI served by `lore ui` that renders the user's knowledge graph as an interactive force-directed visualization. One command, one screenshot, immediate understanding of what your AI brain looks like.

### Success Vision

A user runs `lore ui`, their browser opens, and they see a beautiful, interactive graph of their AI memory. They take a screenshot and post it to Twitter/X. That screenshot generates more interest in Lore than any README ever could.

---

## 2. User Stories

### US-1: Launch the UI
**As a** Lore user
**I want to** run `lore ui` and have my browser open to a graph visualization
**So that** I can see my knowledge graph without any setup

**Acceptance Criteria:**
- `lore ui` starts a local web server and opens the default browser
- `lore ui --port 3333` allows custom port (default: 8766)
- `lore ui --no-open` starts server without opening browser
- Works with both SQLite (local) and Postgres (remote) backends
- Shows loading state while graph data is fetched
- Graceful error if no memories exist ("Your brain is empty. Run `lore remember` to get started.")

### US-2: View the Full Graph
**As a** Lore user
**I want to** see all my memories and entities as an interactive force-directed graph
**So that** I can understand the shape of my knowledge

**Acceptance Criteria:**
- Memory nodes rendered as circles, sized by importance score
- Entity nodes rendered as diamonds/hexagons, sized by mention count
- Edges between entities show relationship type on hover
- Edges between memories and entities show mention connections
- Node colors encode type (memory type or entity type)
- Graph uses force-directed layout with collision avoidance
- Nodes are draggable — user can rearrange the layout
- Zoom and pan with mouse/trackpad
- Minimap in corner for orientation on large graphs

### US-3: Inspect a Node
**As a** Lore user
**I want to** click a node and see its full content
**So that** I can read, audit, and understand individual memories

**Acceptance Criteria:**
- Click a memory node → side panel opens with:
  - Full memory content (rendered as markdown)
  - Type, tier, project, tags
  - Importance score, confidence, upvotes/downvotes
  - Created/updated timestamps
  - Connected entities (linked, clickable)
- Click an entity node → side panel shows:
  - Entity name, type, aliases
  - Description (if enriched)
  - Mention count, first/last seen
  - Connected memories (list, clickable)
  - Connected entities via relationships
- Clicking a linked item in the panel navigates to that node in the graph
- Panel has a close button and closes on Escape key

### US-4: Filter the Graph
**As a** Lore user
**I want to** filter the graph by project, type, date, and importance
**So that** I can focus on a specific subset of my knowledge

**Acceptance Criteria:**
- Filter panel (collapsible sidebar or toolbar):
  - **Project:** dropdown of all projects (multi-select)
  - **Memory type:** checkboxes for each type (general, code, lesson, fact, etc.)
  - **Entity type:** checkboxes (person, tool, project, concept, etc.)
  - **Tier:** checkboxes (working, short, long)
  - **Date range:** start/end date pickers
  - **Importance:** slider (0.0 → 1.0 minimum threshold)
  - **Search:** text input for keyword/semantic search
- Filters apply instantly (debounced, no submit button)
- Filtered-out nodes fade to 10% opacity (not removed) so graph layout stays stable
- Active filter count shown as badge
- "Reset filters" button clears all filters
- URL updates with filter state (shareable/bookmarkable)

### US-5: Search from the UI
**As a** Lore user
**I want to** search my memories from the visualization
**So that** I can find specific knowledge and see it in graph context

**Acceptance Criteria:**
- Search bar prominently placed (top center or top right)
- Supports both keyword and semantic search
- Search results highlight matching nodes in the graph (glow effect)
- Non-matching nodes dim to background
- Results list in side panel with relevance scores
- Click a search result → graph centers on that node
- Search supports the same temporal filters as `lore recall`
- Debounced input (300ms) to avoid excessive API calls

### US-6: Cluster View
**As a** Lore user
**I want to** see my memories grouped by project or topic
**So that** I can understand the distribution of my knowledge

**Acceptance Criteria:**
- Toggle between: Force Layout / Cluster by Project / Cluster by Type
- Cluster view uses hull/convex boundary around grouped nodes
- Cluster labels shown (project name or type name)
- Cluster sizes reflect memory count
- Click a cluster → zoom into that group
- Clusters are color-coded consistently

### US-7: Timeline View
**As a** Lore user
**I want to** see when my memories were created over time
**So that** I can understand my knowledge accumulation pattern

**Acceptance Criteria:**
- Timeline scrubber at bottom of screen
- Drag to select date range → graph shows only memories in that range
- Nodes animate in/out as timeline range changes
- Optional: play button that animates memory accumulation over time
- Timeline shows density (bar chart of memories per day/week)

### US-8: Graph Statistics Dashboard
**As a** Lore user
**I want to** see aggregate statistics about my knowledge graph
**So that** I can understand the health and composition of my memory

**Acceptance Criteria:**
- Stats bar or panel showing:
  - Total memories / entities / relationships
  - Memories by type (pie/donut chart)
  - Memories by project (bar chart)
  - Average importance score
  - Most connected entities (top 5)
  - Recent activity (memories added in last 24h/7d)
- Stats update when filters change

---

## 3. Functional Requirements

### FR-1: Graph Rendering Engine
- Force-directed graph layout with configurable parameters:
  - Link distance: proportional to edge weight (stronger = closer)
  - Charge strength: repulsion between unconnected nodes
  - Center gravity: keeps the graph centered
  - Collision radius: prevents node overlap
- Canvas-based rendering for performance (not SVG for 1000+ nodes)
- WebGL fallback for very large graphs (5000+ nodes)
- Smooth animations on state transitions (filter, zoom, highlight)

### FR-2: Node Visual Encoding

| Property | Visual Channel | Details |
|----------|---------------|---------|
| Node type (memory vs entity) | Shape | Memory = circle, Entity = hexagon |
| Memory type | Color | See color palette in Section 5 |
| Entity type | Color | See color palette in Section 5 |
| Importance score | Size | 8px (low) → 24px (high) |
| Mention count (entity) | Size | 6px (1 mention) → 30px (50+ mentions) |
| Tier | Border style | Working = dashed, Short = dotted, Long = solid |
| Confidence | Opacity | 0.3 (low) → 1.0 (high) |
| Staleness | Glow/ring | Recent = bright ring, old = no ring |
| Selected | Highlight | Gold border + glow |

### FR-3: Edge Visual Encoding

| Property | Visual Channel | Details |
|----------|---------------|---------|
| Relationship type | Color | Matches a consistent palette |
| Weight | Thickness | 0.5px (weak) → 3px (strong) |
| Direction | Arrow | Small arrowhead at target |
| Hover state | Label | Shows rel_type on hover |

### FR-4: Interaction Model
- **Hover node:** Show tooltip (name, type, importance)
- **Click node:** Open detail panel
- **Double-click node:** Expand — fetch and show connected nodes not yet loaded
- **Right-click node:** Context menu (copy ID, open in terminal, forget)
- **Drag node:** Reposition (pin to location)
- **Scroll:** Zoom in/out
- **Click+drag background:** Pan
- **Shift+click:** Multi-select nodes

### FR-5: Data Loading Strategy
- Initial load: Fetch all entities + relationships + memory metadata (no content)
- Lazy load: Memory content fetched only when node is clicked
- Pagination: For graphs > 500 nodes, load in pages with progressive rendering
- Caching: Browser-side cache for fetched memory content (IndexedDB or memory)

### FR-6: CLI Integration
- `lore ui` command in `cli.py`:
  - Starts a lightweight HTTP server serving static files
  - Serves the frontend from a bundled directory (`src/lore/ui/dist/`)
  - Proxies API requests to the existing Lore server (or uses SQLite directly)
  - Opens default browser via `webbrowser.open()`
  - Ctrl+C gracefully shuts down

---

## 4. Non-Functional Requirements

### NFR-1: Performance
| Metric | Target |
|--------|--------|
| Initial render (100 nodes) | < 500ms |
| Initial render (1000 nodes) | < 2s |
| Initial render (5000 nodes) | < 5s |
| Frame rate during interaction | 60fps (canvas), 30fps minimum |
| API response for full graph | < 500ms |
| Memory content fetch (click) | < 200ms |
| Search results | < 500ms |
| Filter application | < 100ms (client-side) |

### NFR-2: Bundle Size
| Asset | Target |
|-------|--------|
| HTML + CSS + JS (minified) | < 500KB total |
| Graph library | < 300KB |
| No external CDN dependencies | Everything bundled |

### NFR-3: Browser Compatibility
- Chrome 90+, Firefox 90+, Safari 15+, Edge 90+
- Mobile browsers: responsive layout, touch zoom/pan
- No IE support

### NFR-4: Accessibility
- Keyboard navigation for node selection (Tab, Enter, Escape)
- Screen reader announcements for selected node details
- Color palette works with common color blindness (tested with Sim Daltonism)
- High contrast mode toggle

### NFR-5: Security
- Served on localhost only (127.0.0.1, not 0.0.0.0) by default
- No external network requests (fully offline-capable)
- No telemetry, no analytics, no tracking
- API key required when connecting to remote Lore server

---

## 5. UI/UX Design

### Layout

```
┌──────────────────────────────────────────────────────────────┐
│  🧠 Lore    [Search...........................] [⚙] [Stats] │
├──────────┬──────────────────────────────────────┬────────────┤
│          │                                      │            │
│ Filters  │                                      │  Detail    │
│          │        Force-Directed Graph           │  Panel     │
│ Project  │              Canvas                   │            │
│ ☑ all    │                                      │ (appears   │
│ Type     │         ◆ entity                     │  on node   │
│ ☑ code   │        / \                           │  click)    │
│ ☑ lesson │   ● ──◆───● ──◆                     │            │
│ ☑ fact   │      /       \                       │            │
│ Tier     │    ●           ●                     │            │
│ ☑ long   │                                      │            │
│          │   [minimap]                           │            │
│ Imp.     │                                      │            │
│ ━━━○━━━  │                                      │            │
│ 0.3  1.0 │                                      │            │
│          │                                      │            │
│[Reset]   │                                      │            │
├──────────┴──────────────────────────────────────┴────────────┤
│ Timeline: ▁▂▃▅▇▅▃▂▁▁▂▃▅▃  [◄──────○──────────────►]       │
└──────────────────────────────────────────────────────────────┘
```

- **Filter sidebar:** 220px wide, left side, collapsible
- **Detail panel:** 320px wide, right side, slides in on node click
- **Graph canvas:** Fills remaining space
- **Timeline:** 48px tall, bottom, collapsible
- **Header:** 48px tall, contains search + controls

### Color Palette

Dark theme by default (looks best in screenshots, matches developer tools aesthetic).

**Background:** `#0a0a0f` (near-black with blue tint)

**Memory type colors:**
| Type | Color | Hex |
|------|-------|-----|
| general | Soft blue | `#6b8afd` |
| code | Electric green | `#4ade80` |
| lesson | Warm amber | `#fbbf24` |
| fact | Cool cyan | `#22d3ee` |
| convention | Soft purple | `#a78bfa` |
| preference | Pink | `#f472b6` |
| debug | Red-orange | `#fb7185` |
| pattern | Teal | `#2dd4bf` |
| note | Light gray | `#94a3b8` |

**Entity type colors:**
| Type | Color | Hex |
|------|-------|-----|
| person | Gold | `#fcd34d` |
| tool | Electric blue | `#60a5fa` |
| project | Green | `#34d399` |
| concept | Purple | `#c084fc` |
| organization | Orange | `#fb923c` |
| platform | Indigo | `#818cf8` |
| language | Cyan | `#67e8f9` |
| framework | Rose | `#fda4af` |
| service | Emerald | `#6ee7b7` |
| other | Gray | `#9ca3af` |

**Edge colors:** Desaturated versions of source node color, 40% opacity, brighten on hover.

**Glow effects:**
- Selected node: `box-shadow: 0 0 20px <node-color>` at 60% opacity
- Search match: Pulsing glow animation (1.5s ease-in-out infinite)
- High importance (>0.8): Subtle constant glow

### Typography
- Font: System font stack (`-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`)
- Node labels: 11px, truncated at 16 characters, full name on hover
- Panel text: 13px body, 16px headings
- Monospace for IDs and code content: `'SF Mono', 'Fira Code', monospace`

### Responsive Behavior
- Desktop (>1024px): Full layout as above
- Tablet (768-1024px): Filter sidebar collapses to icon bar, detail panel overlays graph
- Mobile (<768px): Graph fills screen, filters and details are bottom sheets

### Animations
- Node enter: Fade in + scale from 0.5 → 1.0 (200ms ease-out)
- Node exit (filtered): Fade to 10% opacity (300ms)
- Panel slide: 250ms ease-out
- Graph layout transition: 500ms spring physics
- Search highlight pulse: 1.5s ease-in-out infinite

---

## 6. API Design

All new endpoints live under the existing Lore server. Prefix: `/v1/ui/`.

### GET /v1/ui/graph

Returns the full graph structure for visualization.

**Query Parameters:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| project | string | null | Filter by project |
| type | string | null | Filter by memory type |
| entity_type | string | null | Filter by entity type |
| tier | string | null | Filter by memory tier |
| since | string (ISO date) | null | Only memories after this date |
| until | string (ISO date) | null | Only memories before this date |
| min_importance | float | 0.0 | Minimum importance score |
| limit | int | 1000 | Max nodes to return |
| include_orphans | bool | true | Include memories with no graph edges |

**Response:**
```json
{
  "nodes": [
    {
      "id": "mem_abc123",
      "kind": "memory",
      "label": "Redis caching strategy for auth tokens",
      "type": "code",
      "tier": "long",
      "project": "auth-service",
      "importance": 0.85,
      "confidence": 0.95,
      "tags": ["redis", "caching"],
      "created_at": "2026-03-10T14:30:00Z",
      "upvotes": 3,
      "downvotes": 0,
      "access_count": 12
    },
    {
      "id": "ent_def456",
      "kind": "entity",
      "label": "Redis",
      "type": "tool",
      "mention_count": 14,
      "aliases": ["redis-server", "Redis Cache"],
      "first_seen_at": "2026-01-15T09:00:00Z",
      "last_seen_at": "2026-03-10T14:30:00Z"
    }
  ],
  "edges": [
    {
      "source": "ent_def456",
      "target": "ent_ghi789",
      "rel_type": "depends_on",
      "weight": 0.8,
      "label": "depends_on"
    },
    {
      "source": "mem_abc123",
      "target": "ent_def456",
      "rel_type": "mentions",
      "weight": 1.0,
      "label": "mentions"
    }
  ],
  "stats": {
    "total_memories": 847,
    "total_entities": 123,
    "total_relationships": 456,
    "filtered_nodes": 234,
    "filtered_edges": 189
  }
}
```

**Design notes:**
- Memory `label` is first 60 chars of content (full content fetched on click)
- `kind` field distinguishes memory vs entity nodes for frontend rendering
- Stats block tells frontend total vs filtered counts for UI indicators

### GET /v1/ui/graph/clusters

Returns nodes pre-grouped for cluster visualization.

**Query Parameters:** Same as `/v1/ui/graph` plus:
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| group_by | string | "project" | "project", "type", or "entity_type" |

**Response:**
```json
{
  "clusters": [
    {
      "id": "cluster_auth-service",
      "label": "auth-service",
      "group_by": "project",
      "node_count": 45,
      "node_ids": ["mem_abc123", "ent_def456", ...],
      "center": {"x": 0.3, "y": 0.5}
    }
  ],
  "nodes": [...],
  "edges": [...]
}
```

### GET /v1/ui/memory/{id}

Returns full memory content for the detail panel.

**Response:**
```json
{
  "id": "mem_abc123",
  "content": "Full memory content here...",
  "type": "code",
  "tier": "long",
  "project": "auth-service",
  "tags": ["redis", "caching"],
  "importance_score": 0.85,
  "confidence": 0.95,
  "upvotes": 3,
  "downvotes": 0,
  "access_count": 12,
  "created_at": "2026-03-10T14:30:00Z",
  "updated_at": "2026-03-12T09:15:00Z",
  "source": "claude-code",
  "metadata": {...},
  "connected_entities": [
    {"id": "ent_def456", "name": "Redis", "type": "tool", "rel_type": "mentions"}
  ],
  "connected_memories": [
    {"id": "mem_xyz789", "label": "Auth token expiry policy", "type": "lesson", "rel_type": "related_to"}
  ]
}
```

### GET /v1/ui/entity/{id}

Returns full entity details for the detail panel.

**Response:**
```json
{
  "id": "ent_def456",
  "name": "Redis",
  "entity_type": "tool",
  "aliases": ["redis-server", "Redis Cache"],
  "description": "In-memory data store used for caching",
  "mention_count": 14,
  "first_seen_at": "2026-01-15T09:00:00Z",
  "last_seen_at": "2026-03-10T14:30:00Z",
  "connected_entities": [
    {"id": "ent_ghi789", "name": "auth-service", "type": "service", "rel_type": "depends_on", "weight": 0.8}
  ],
  "connected_memories": [
    {"id": "mem_abc123", "label": "Redis caching strategy...", "type": "code", "importance": 0.85}
  ]
}
```

### POST /v1/ui/search

Search memories and entities from the UI.

**Request:**
```json
{
  "query": "redis caching",
  "mode": "semantic",
  "limit": 20,
  "filters": {
    "project": "auth-service",
    "type": null,
    "tier": null,
    "since": null,
    "until": null,
    "min_importance": 0.0
  }
}
```

**Response:**
```json
{
  "results": [
    {
      "id": "mem_abc123",
      "kind": "memory",
      "label": "Redis caching strategy for auth tokens",
      "type": "code",
      "score": 0.92,
      "importance": 0.85,
      "project": "auth-service"
    }
  ],
  "total": 5,
  "query_time_ms": 45
}
```

### GET /v1/ui/stats

Returns aggregate statistics for the dashboard panel.

**Response:**
```json
{
  "total_memories": 847,
  "total_entities": 123,
  "total_relationships": 456,
  "by_type": {"code": 234, "lesson": 123, "fact": 89, ...},
  "by_project": {"auth-service": 145, "frontend": 98, ...},
  "by_tier": {"working": 12, "short": 89, "long": 746},
  "by_entity_type": {"tool": 34, "concept": 28, ...},
  "avg_importance": 0.67,
  "top_entities": [
    {"name": "Redis", "type": "tool", "mention_count": 14},
    {"name": "Python", "type": "language", "mention_count": 12}
  ],
  "recent_24h": 15,
  "recent_7d": 67,
  "oldest_memory": "2026-01-05T...",
  "newest_memory": "2026-03-14T..."
}
```

### GET /v1/ui/timeline

Returns memory density over time for the timeline scrubber.

**Query Parameters:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| bucket | string | "day" | "hour", "day", "week", "month" |
| project | string | null | Filter by project |

**Response:**
```json
{
  "buckets": [
    {"date": "2026-03-01", "count": 12, "by_type": {"code": 5, "lesson": 3, ...}},
    {"date": "2026-03-02", "count": 8, ...}
  ],
  "range": {
    "start": "2026-01-05",
    "end": "2026-03-14"
  }
}
```

---

## 7. Technology Choices

### Graph Rendering: D3.js + Canvas

**Choice:** D3-force for layout calculation + HTML Canvas for rendering.

**Why D3:**
- Industry standard for data visualization
- Force simulation is battle-tested at scale
- ~70KB minified + gzipped (acceptable)
- No framework dependency
- Lore already has `to_d3_json()` producing D3-compatible output
- Massive ecosystem of examples and documentation

**Why Canvas over SVG:**
- SVG degrades badly above ~500 nodes (DOM overhead)
- Canvas renders 5000+ nodes at 60fps
- GPU-accelerated on modern browsers
- Hit testing via quadtree (D3 provides this)

**Alternative considered:** Sigma.js (purpose-built for large graph rendering, WebGL). Rejected because D3 is sufficient for our target (1000-5000 nodes) and has broader community knowledge. If we hit WebGL needs later, we can swap the renderer without changing the data layer.

### Frontend: Vanilla JS + Single HTML File

**Choice:** No framework. Single `index.html` with inlined CSS and JS, or a small set of static files.

**Why no framework:**
- This is a read-only visualization, not an application
- No component lifecycle, no state management needed beyond the graph
- Bundle size must stay under 500KB
- Framework overhead (React: 45KB, Vue: 34KB) is wasted
- Deployment is trivial: serve static files
- One less build tool dependency

**Build approach:**
- Source in `src/lore/ui/src/` — modular JS files
- Build to `src/lore/ui/dist/` — single minified bundle via esbuild (fast, zero-config)
- `esbuild` is ~8MB, builds in <100ms, no node_modules bloat
- Built files checked into git (so `pip install lore-memory` just works, no npm needed at install time)

### CSS: Custom, Dark-First

No CSS framework. Hand-written CSS (~200 lines) for the layout and panel components. CSS custom properties for theming.

### Server: Existing FastAPI

New route module `src/lore/server/routes/ui.py` with the endpoints above. Static file serving via FastAPI's `StaticFiles` mount for the frontend assets. The `lore ui` CLI command starts the same server as `lore serve` but also:
1. Mounts the static UI files
2. Opens the browser
3. Logs to stdout with a "UI available at http://localhost:8766" message

### For Local-Only (SQLite) Users

The `lore ui` command must also work for users who only use SQLite (no Postgres server). Strategy:
- `lore ui` starts a minimal FastAPI server that reads directly from SQLite
- Shares the same route handlers but instantiates a SqliteStore instead of PgStore
- This means the UI endpoints work identically regardless of backend

---

## 8. Success Metrics

### Launch Metrics (First 30 Days)

| Metric | Target | Measurement |
|--------|--------|-------------|
| Screenshot shares (Twitter/X, Discord) | 10+ organic shares | Manual tracking |
| `lore ui` command usage | 50% of active users try it once | Opt-in telemetry / GitHub discussions |
| README click-through from graph screenshot | 2x improvement in GitHub stars growth rate | GitHub Insights |
| Time to first visualization | < 5 seconds from `lore ui` to rendered graph | User testing |

### Quality Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Render performance (1000 nodes) | < 2s initial, 60fps interaction | Automated performance test |
| Bundle size | < 500KB total | CI check |
| API response time (full graph) | p95 < 500ms | Server logs |
| Zero external network requests | 0 outbound connections | Network audit |
| Browser compatibility | Chrome, Firefox, Safari, Edge latest | CI cross-browser test |

### User Satisfaction (Qualitative)

- Users describe the visualization as "beautiful" or "impressive" in feedback
- Users discover unexpected connections in their knowledge graph
- Users feel more confident about what Lore stores after using the UI
- At least one user says "I finally understand what Lore does" after seeing the graph

---

## 9. Implementation Phases

### Phase 1: Foundation (MVP)
- `lore ui` CLI command + static file server
- `/v1/ui/graph` endpoint
- Force-directed graph rendering (D3 + Canvas)
- Node type/color encoding
- Zoom, pan, drag
- Click → detail panel with full memory content

### Phase 2: Interactivity
- Filter sidebar (project, type, tier, importance)
- Search bar with semantic search
- `/v1/ui/search` endpoint
- Node highlighting on search match
- `/v1/ui/memory/{id}` and `/v1/ui/entity/{id}` endpoints

### Phase 3: Polish
- Cluster view (`/v1/ui/graph/clusters`)
- Timeline scrubber (`/v1/ui/timeline`)
- Stats dashboard (`/v1/ui/stats`)
- Minimap
- Responsive layout (tablet/mobile)
- Keyboard navigation
- Performance optimization for large graphs

---

## 10. Open Questions

1. **Offline-first or server-required?** Should the UI work by embedding SQLite data directly into the page (via sql.js), or always require a running server? Current decision: always require server (simpler, consistent API). Revisit if users complain.

2. **Theme toggle?** Dark theme is best for screenshots, but some users may prefer light theme. Should we ship both? Current decision: dark only for v1, add light theme if requested.

3. **Graph layout persistence?** Should manually dragged node positions be saved? Where — localStorage, server-side? Current decision: localStorage only, no server persistence.

4. **Real-time updates?** Should the graph update live as new memories are added (WebSocket)? Current decision: No, manual refresh only for v1. Add WebSocket in v1.1 if needed.

5. **Embed in Obsidian?** The product brief mentions iframe embedding in Obsidian. Worth pursuing? Current decision: Defer. Focus on standalone UI first. The localhost URL can be iframed by power users manually.

6. **Graph data pagination strategy?** For users with 10,000+ memories, sending the full graph in one response may be slow. Options: (a) server-side pagination with "load more", (b) importance-based pruning (only show top N), (c) ego-graph starting from a seed node. Current decision: Default to importance-based pruning (top 1000 nodes by importance + all entities), with `limit` parameter to adjust.

7. **Memory actions from UI?** Should users be able to upvote, downvote, forget, or edit memories from the UI? Current decision: Read-only for v1. Add write actions in v1.1.

8. **Authentication for local use?** The UI runs on localhost. Should it require auth? Current decision: No auth for localhost. Require API key if `--host 0.0.0.0` is used (exposes to network).

---

## Appendix A: Existing Infrastructure to Leverage

| What | Where | How E1 Uses It |
|------|-------|----------------|
| `to_d3_json()` | `src/lore/graph/visualization.py` | Backend uses this to generate graph data — extend for memory nodes |
| `GraphTraverser.traverse()` | `src/lore/graph/traverser.py` | Powers ego-graph expansion (double-click to expand) |
| `EntityCache.get_all()` | `src/lore/graph/cache.py` | Fast entity listing for initial load |
| FastAPI server | `src/lore/server/app.py` | Mount new routes + static files |
| `Store.list()` | `src/lore/store/base.py` | Memory listing with filters |
| `POST /v1/memories/search` | `src/lore/server/routes/memories.py` | Powers UI search (semantic) |
| Entity/Relationship types | `src/lore/types.py` | Node color/shape mapping |
| `lore serve` CLI | `src/lore/cli.py` | Extend with `lore ui` variant |

## Appendix B: Competitive Landscape

| Product | Graph View | Our Advantage |
|---------|-----------|---------------|
| Obsidian | Markdown file links only, no typed relationships | Lore has typed entities, weighted relationships, importance scores — richer visualization |
| Mem0 | No visualization | First-mover in AI memory visualization |
| Zep | No visualization | First-mover in AI memory visualization |
| Neo4j Browser | Full graph DB UI | Too complex for end users; Lore's UI is purpose-built for AI memory |
| Roam Research | Page graph | No AI integration, no semantic search from graph |

Our unique angle: **This is the first visual representation of an AI agent's memory.** Not documents, not notes — memories. That's a new category of visualization.
