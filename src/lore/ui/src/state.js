// Application state management with EventTarget pub/sub

export class AppState extends EventTarget {
  constructor() {
    super();
    this.nodes = [];
    this.edges = [];
    this.stats = {};
    this.filteredNodeIds = new Set();
    this.selectedNodeId = null;
    this.hoveredNodeId = null;
    this.hoveredEdge = null;
    this.searchResults = new Set();
    this.searchQuery = '';
    this.focusedNodeId = null;
    this.neighborIds = new Set();
    this.viewMode = 'force'; // 'force', 'cluster-project', 'cluster-type'
    this.filters = {
      project: null,
      types: new Set(),
      entityTypes: new Set(),
      tiers: new Set(),
      minImportance: 0,
      dateRange: [null, null],
    };
    this._nodeMap = new Map();
  }

  setGraphData(nodes, edges, stats) {
    this.nodes = nodes;
    this.edges = edges;
    this.stats = stats;
    this._nodeMap.clear();
    for (const n of nodes) this._nodeMap.set(n.id, n);
    this._recomputeFiltered();
    this.dispatchEvent(new CustomEvent('dataLoaded'));
  }

  getNode(id) {
    return this._nodeMap.get(id);
  }

  selectNode(id) {
    this.selectedNodeId = id;
    this.dispatchEvent(new CustomEvent('selectionChange', { detail: { id } }));
  }

  setFocus(id, neighborIds) {
    this.focusedNodeId = id;
    this.neighborIds = new Set(neighborIds || []);
    if (id) this.neighborIds.add(id);
    this.dispatchEvent(new CustomEvent('focusChange', { detail: { id, neighborIds: this.neighborIds } }));
  }

  clearFocus() {
    this.focusedNodeId = null;
    this.neighborIds.clear();
    this.dispatchEvent(new CustomEvent('focusChange', { detail: { id: null } }));
  }

  setHoveredNode(id) {
    if (this.hoveredNodeId !== id) {
      this.hoveredNodeId = id;
      this.dispatchEvent(new CustomEvent('hoverChange', { detail: { id } }));
    }
  }

  setHoveredEdge(edge) {
    this.hoveredEdge = edge;
    this.dispatchEvent(new CustomEvent('hoverChange', { detail: { edge } }));
  }

  setFilter(key, value) {
    this.filters[key] = value;
    this._recomputeFiltered();
    this._syncUrlState();
    this.dispatchEvent(new CustomEvent('filterChange'));
  }

  resetFilters() {
    this.filters = {
      project: null,
      types: new Set(),
      entityTypes: new Set(),
      tiers: new Set(),
      minImportance: 0,
      dateRange: [null, null],
    };
    this._recomputeFiltered();
    this._syncUrlState();
    this.dispatchEvent(new CustomEvent('filterChange'));
  }

  setSearchResults(ids, query) {
    this.searchResults = new Set(ids);
    this.searchQuery = query;
    this.dispatchEvent(new CustomEvent('searchChange'));
  }

  clearSearch() {
    this.searchResults.clear();
    this.searchQuery = '';
    this.dispatchEvent(new CustomEvent('searchChange'));
  }

  setViewMode(mode) {
    this.viewMode = mode;
    this.dispatchEvent(new CustomEvent('viewModeChange', { detail: { mode } }));
  }

  getActiveFilterCount() {
    let count = 0;
    if (this.filters.project) count++;
    if (this.filters.types.size > 0) count++;
    if (this.filters.entityTypes.size > 0) count++;
    if (this.filters.tiers.size > 0) count++;
    if (this.filters.minImportance > 0) count++;
    if (this.filters.dateRange[0] || this.filters.dateRange[1]) count++;
    return count;
  }

  _recomputeFiltered() {
    const ids = new Set();
    for (const node of this.nodes) {
      if (this._matchesFilters(node)) {
        ids.add(node.id);
      }
    }
    this.filteredNodeIds = ids;
  }

  _matchesFilters(node) {
    const f = this.filters;
    if (node.kind === 'memory') {
      if (f.project && node.project !== f.project) return false;
      if (f.types.size > 0 && !f.types.has(node.type)) return false;
      if (f.tiers.size > 0 && !f.tiers.has(node.tier)) return false;
      if (f.minImportance > 0 && (node.importance || 0) < f.minImportance) return false;
      if (f.dateRange[0] && node.created_at < f.dateRange[0]) return false;
      if (f.dateRange[1] && node.created_at > f.dateRange[1]) return false;
    }
    if (node.kind === 'entity') {
      if (f.entityTypes.size > 0 && !f.entityTypes.has(node.type)) return false;
    }
    return true;
  }

  _syncUrlState() {
    const params = new URLSearchParams();
    if (this.filters.project) params.set('project', this.filters.project);
    if (this.filters.types.size > 0) params.set('type', [...this.filters.types].join(','));
    if (this.filters.entityTypes.size > 0) params.set('entity_type', [...this.filters.entityTypes].join(','));
    if (this.filters.tiers.size > 0) params.set('tier', [...this.filters.tiers].join(','));
    if (this.filters.minImportance > 0) params.set('min_importance', this.filters.minImportance);
    if (this.filters.dateRange[0]) params.set('since', this.filters.dateRange[0]);
    if (this.filters.dateRange[1]) params.set('until', this.filters.dateRange[1]);
    if (this.searchQuery) params.set('search', this.searchQuery);

    const qs = params.toString();
    const hash = this.selectedNodeId ? `#node=${this.selectedNodeId}` : '';
    const url = qs ? `?${qs}${hash}` : `${window.location.pathname}${hash}`;
    history.replaceState(null, '', url);
  }

  restoreFromUrl() {
    const params = new URLSearchParams(window.location.search);
    if (params.has('project')) this.filters.project = params.get('project');
    if (params.has('type')) this.filters.types = new Set(params.get('type').split(','));
    if (params.has('entity_type')) this.filters.entityTypes = new Set(params.get('entity_type').split(','));
    if (params.has('tier')) this.filters.tiers = new Set(params.get('tier').split(','));
    if (params.has('min_importance')) this.filters.minImportance = parseFloat(params.get('min_importance'));
    if (params.has('since')) this.filters.dateRange[0] = params.get('since');
    if (params.has('until')) this.filters.dateRange[1] = params.get('until');
    if (params.has('search')) this.searchQuery = params.get('search');

    const hash = window.location.hash;
    if (hash.startsWith('#node=')) {
      this.selectedNodeId = hash.slice(6);
    }

    this._recomputeFiltered();
  }
}
