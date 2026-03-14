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

export { cache };
