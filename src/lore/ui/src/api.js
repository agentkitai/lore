// API client with LRU cache

class LRUCache {
  constructor(maxSize = 200) {
    this.max = maxSize;
    this._map = new Map();
  }

  get(key) {
    if (!this._map.has(key)) return undefined;
    const val = this._map.get(key);
    // Move to end (most recent)
    this._map.delete(key);
    this._map.set(key, val);
    return val;
  }

  set(key, val) {
    if (this._map.has(key)) this._map.delete(key);
    this._map.set(key, val);
    if (this._map.size > this.max) {
      // Delete oldest
      const first = this._map.keys().next().value;
      this._map.delete(first);
    }
  }

  get size() { return this._map.size; }
}

const cache = new LRUCache(200);

async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    const err = new Error(`API error: ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

export async function fetchGraph(params = {}) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v != null) qs.set(k, v);
  }
  const url = `/v1/ui/graph${qs.toString() ? '?' + qs : ''}`;
  return fetchJSON(url);
}

export async function fetchMemoryDetail(id) {
  const cached = cache.get(`mem:${id}`);
  if (cached) return cached;
  const data = await fetchJSON(`/v1/ui/memory/${id}`);
  cache.set(`mem:${id}`, data);
  return data;
}

export async function fetchEntityDetail(id) {
  const cached = cache.get(`ent:${id}`);
  if (cached) return cached;
  const data = await fetchJSON(`/v1/ui/entity/${id}`);
  cache.set(`ent:${id}`, data);
  return data;
}

export async function searchMemories(query, mode = 'keyword', limit = 20, filters = {}) {
  return fetchJSON('/v1/ui/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, mode, limit, filters }),
  });
}

export async function fetchClusters(groupBy = 'project') {
  return fetchJSON(`/v1/ui/graph/clusters?group_by=${groupBy}`);
}

export async function fetchStats(project = null) {
  const qs = project ? `?project=${encodeURIComponent(project)}` : '';
  return fetchJSON(`/v1/ui/stats${qs}`);
}

export async function fetchTimeline(bucket = 'day', project = null) {
  const params = new URLSearchParams({ bucket });
  if (project) params.set('project', project);
  return fetchJSON(`/v1/ui/timeline?${params}`);
}

export async function fetchTopics(minMentions = 3, limit = 20) {
  const params = new URLSearchParams({ min_mentions: minMentions, limit });
  return fetchJSON(`/v1/ui/topics?${params}`);
}

export async function fetchTopicDetail(name, maxMemories = 20) {
  const params = new URLSearchParams({ max_memories: maxMemories });
  return fetchJSON(`/v1/ui/topics/${encodeURIComponent(name)}?${params}`);
}

// Review queue (E6)
export async function fetchReviewQueue(limit = 50) {
  return fetchJSON(`/v1/review?limit=${limit}`);
}

export async function reviewRelationship(id, action, reason = null) {
  return fetchJSON(`/v1/review/${id}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, reason }),
  });
}

export async function reviewBulk(action, ids, reason = null) {
  return fetchJSON('/v1/review/bulk', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, ids, reason }),
  });
}

// Fetch neighbors of a node (connected memories + entities)
// Returns { ids: string[], nodes: {id, label, kind, type}[] }
export async function fetchNeighbors(id) {
  const cached = cache.get(`nbr:${id}`);
  if (cached) return cached;

  const ids = [];
  const nodes = [];

  // Try memory detail first (has connected_entities + connected_memories)
  try {
    const data = await fetchMemoryDetail(id);
    if (data.connected_entities) {
      for (const e of data.connected_entities) {
        ids.push(e.id);
        nodes.push({ id: e.id, label: e.name || e.type, kind: 'entity', type: e.type || 'unknown' });
      }
    }
    if (data.connected_memories) {
      for (const m of data.connected_memories) {
        ids.push(m.id);
        nodes.push({ id: m.id, label: (m.label || '').slice(0, 40), kind: 'memory', type: m.type || 'general' });
      }
    }
  } catch (_memErr) {
    // Try entity detail
    try {
      const data = await fetchEntityDetail(id);
      if (data.connected_memories) {
        for (const m of data.connected_memories) {
          ids.push(m.id);
          nodes.push({ id: m.id, label: (m.label || '').slice(0, 40), kind: 'memory', type: m.type || 'general' });
        }
      }
      if (data.connected_entities) {
        for (const e of data.connected_entities) {
          ids.push(e.id);
          nodes.push({ id: e.id, label: e.name || e.type, kind: 'entity', type: e.type || 'unknown' });
        }
      }
    } catch (_entErr) {
      // nothing
    }
  }

  const result = { ids, nodes };
  cache.set(`nbr:${id}`, result);
  return result;
}

export { cache };
