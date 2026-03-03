/**
 * Remote HTTP store for the generalized Memory API.
 * Talks to a Lore server via the /v1/memories REST endpoints.
 */

import type {
  Memory,
  SearchResult,
  StoreStats,
  ListMemoriesOptions,
} from './types.js';
import { LoreConnectionError, LoreAuthError } from './errors.js';

export interface RemoteMemoryStoreOptions {
  apiUrl: string;
  apiKey: string;
  timeoutMs?: number;
}

interface RequestOptions {
  method: string;
  path: string;
  body?: unknown;
  params?: Record<string, string>;
}

function responseToMemory(data: Record<string, unknown>): Memory {
  return {
    id: data.id as string,
    content: data.content as string,
    type: (data.type as string) ?? 'note',
    source: (data.source as string | null) ?? null,
    project: (data.project as string | null) ?? null,
    tags: (data.tags as string[]) ?? [],
    metadata: (data.metadata as Record<string, unknown>) ?? {},
    embedding: null,
    createdAt: String(data.created_at ?? ''),
    updatedAt: String(data.updated_at ?? ''),
    expiresAt: data.expires_at != null ? String(data.expires_at) : null,
  };
}

/**
 * HTTP-backed memory store that delegates to a Lore server's /v1/memories endpoints.
 */
export class RemoteMemoryStore {
  private readonly apiUrl: string;
  private readonly apiKey: string;
  private readonly timeoutMs: number;

  constructor(options: RemoteMemoryStoreOptions) {
    this.apiUrl = options.apiUrl.replace(/\/+$/, '');
    this.apiKey = options.apiKey;
    this.timeoutMs = options.timeoutMs ?? 30000;
  }

  private async request(opts: RequestOptions): Promise<Response> {
    let url = `${this.apiUrl}${opts.path}`;
    if (opts.params) {
      const qs = new URLSearchParams(opts.params).toString();
      if (qs) url += `?${qs}`;
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);

    let resp: Response;
    try {
      resp = await fetch(url, {
        method: opts.method,
        headers: {
          Authorization: `Bearer ${this.apiKey}`,
          'Content-Type': 'application/json',
        },
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
        signal: controller.signal,
      });
    } catch (err: unknown) {
      const error = err as Error;
      if (error.name === 'AbortError') {
        throw new LoreConnectionError(`Request timed out: ${url}`);
      }
      throw new LoreConnectionError(`Cannot connect to ${this.apiUrl}: ${error.message}`);
    } finally {
      clearTimeout(timer);
    }

    if (resp.status === 401 || resp.status === 403) {
      const text = await resp.text();
      throw new LoreAuthError(`Authentication failed (${resp.status}): ${text}`);
    }

    return resp;
  }

  /** Store a memory via POST /v1/memories. Returns the memory ID. */
  async save(opts: {
    content: string;
    type?: string;
    tags?: string[];
    metadata?: Record<string, unknown>;
    source?: string;
    project?: string;
    expiresAt?: string;
  }): Promise<string> {
    const payload: Record<string, unknown> = {
      content: opts.content,
      type: opts.type ?? 'note',
      tags: opts.tags ?? [],
      metadata: opts.metadata ?? {},
    };
    if (opts.source) payload.source = opts.source;
    if (opts.project) payload.project = opts.project;
    if (opts.expiresAt) payload.expires_at = opts.expiresAt;

    const resp = await this.request({ method: 'POST', path: '/v1/memories', body: payload });
    if (!resp.ok) {
      throw new Error(`Save failed (${resp.status}): ${await resp.text()}`);
    }
    const data = (await resp.json()) as Record<string, unknown>;
    return data.id as string;
  }

  /** Get a memory by ID via GET /v1/memories/:id. */
  async get(memoryId: string): Promise<Memory | null> {
    const resp = await this.request({ method: 'GET', path: `/v1/memories/${memoryId}` });
    if (resp.status === 404) return null;
    if (!resp.ok) throw new Error(`Get failed (${resp.status}): ${await resp.text()}`);
    return responseToMemory((await resp.json()) as Record<string, unknown>);
  }

  /** Semantic search via GET /v1/memories/search?q=... */
  async search(opts: {
    query: string;
    type?: string;
    tags?: string[];
    project?: string;
    limit?: number;
  }): Promise<SearchResult[]> {
    const params: Record<string, string> = {
      q: opts.query,
      limit: String(opts.limit ?? 5),
    };
    if (opts.type) params.type = opts.type;
    if (opts.tags && opts.tags.length > 0) params.tags = opts.tags.join(',');
    if (opts.project) params.project = opts.project;

    const resp = await this.request({ method: 'GET', path: '/v1/memories/search', params });
    if (!resp.ok) throw new Error(`Search failed (${resp.status}): ${await resp.text()}`);
    const data = (await resp.json()) as { memories: Array<Record<string, unknown>> };
    return data.memories.map((item) => ({
      memory: responseToMemory(item),
      score: (item.score as number) ?? 0,
    }));
  }

  /** List memories via GET /v1/memories. */
  async list(opts?: ListMemoriesOptions): Promise<{ memories: Memory[]; total: number }> {
    const params: Record<string, string> = {};
    if (opts?.type) params.type = opts.type;
    if (opts?.tags && opts.tags.length > 0) params.tags = opts.tags.join(',');
    if (opts?.project) params.project = opts.project;
    if (opts?.limit != null) params.limit = String(opts.limit);
    if (opts?.offset != null) params.offset = String(opts.offset);
    if (opts?.includeExpired) params.include_expired = 'true';

    const resp = await this.request({ method: 'GET', path: '/v1/memories', params });
    if (!resp.ok) throw new Error(`List failed (${resp.status}): ${await resp.text()}`);
    const data = (await resp.json()) as { memories: Array<Record<string, unknown>>; total?: number };
    const memories = data.memories.map(responseToMemory);
    return { memories, total: data.total ?? memories.length };
  }

  /** Delete a single memory via DELETE /v1/memories/:id. */
  async delete(memoryId: string): Promise<boolean> {
    const resp = await this.request({ method: 'DELETE', path: `/v1/memories/${memoryId}` });
    if (resp.status === 404) return false;
    if (!resp.ok) throw new Error(`Delete failed (${resp.status}): ${await resp.text()}`);
    return true;
  }

  /** Bulk delete via DELETE /v1/memories?confirm=true with filters. */
  async deleteByFilter(opts?: {
    type?: string;
    tags?: string[];
    project?: string;
  }): Promise<number> {
    const params: Record<string, string> = { confirm: 'true' };
    if (opts?.type) params.type = opts.type;
    if (opts?.tags && opts.tags.length > 0) params.tags = opts.tags.join(',');
    if (opts?.project) params.project = opts.project;

    const resp = await this.request({ method: 'DELETE', path: '/v1/memories', params });
    if (!resp.ok) throw new Error(`Delete failed (${resp.status}): ${await resp.text()}`);
    const data = (await resp.json()) as { deleted?: number };
    return data.deleted ?? 0;
  }

  /** Get statistics via GET /v1/stats. */
  async stats(project?: string): Promise<StoreStats> {
    const params: Record<string, string> = {};
    if (project) params.project = project;

    const resp = await this.request({ method: 'GET', path: '/v1/stats', params });
    if (!resp.ok) throw new Error(`Stats failed (${resp.status}): ${await resp.text()}`);
    const data = (await resp.json()) as Record<string, unknown>;
    return {
      totalCount: (data.total_count as number) ?? 0,
      countByType: (data.count_by_type as Record<string, number>) ?? {},
      countByProject: (data.count_by_project as Record<string, number>) ?? {},
      oldestMemory: (data.oldest_memory as string | null) ?? null,
      newestMemory: (data.newest_memory as string | null) ?? null,
    };
  }

  async close(): Promise<void> {
    // No persistent connection to close with fetch
  }
}
