/**
 * Remote HTTP store implementation.
 * Mirrors Python RemoteStore — delegates to a Lore Cloud server via REST API.
 */

import type { Store } from './base.js';
import type { Memory, ListOptions } from '../types.js';
import { LoreConnectionError, LoreAuthError, MemoryNotFoundError } from '../errors.js';
import { deserializeEmbedding } from '../embed.js';

/** Options for constructing a RemoteStore. */
export interface RemoteStoreOptions {
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

function memoryToServerDict(memory: Memory): Record<string, unknown> {
  const d: Record<string, unknown> = {
    content: memory.content,
    type: memory.type,
    context: memory.context,
    tags: memory.tags,
    metadata: memory.metadata ?? {},
    confidence: memory.confidence,
    source: memory.source,
    project: memory.project,
    created_at: memory.createdAt,
    updated_at: memory.updatedAt,
    ttl: memory.ttl,
    expires_at: memory.expiresAt,
    upvotes: memory.upvotes,
    downvotes: memory.downvotes,
  };
  if (memory.embedding !== null && memory.embedding.length > 0) {
    d.embedding = deserializeEmbedding(memory.embedding);
  } else {
    d.embedding = [];
  }
  return d;
}

function serverResponseToMemory(data: Record<string, unknown>): Memory {
  const createdAt = String(data.created_at ?? '');
  const updatedAt = String(data.updated_at ?? '');
  const expiresAt = data.expires_at != null ? String(data.expires_at) : null;

  return {
    id: data.id as string,
    content: data.content as string,
    type: (data.type as string) ?? 'general',
    context: (data.context as string | null) ?? null,
    tags: (data.tags as string[]) ?? [],
    metadata: (data.metadata as Record<string, unknown> | null) ?? null,
    confidence: (data.confidence as number) ?? 1.0,
    source: (data.source as string | null) ?? null,
    project: (data.project as string | null) ?? null,
    embedding: null,
    createdAt,
    updatedAt,
    ttl: (data.ttl as number) ?? null,
    expiresAt,
    upvotes: (data.upvotes as number) ?? 0,
    downvotes: (data.downvotes as number) ?? 0,
  };
}

/** HTTP-backed memory store that delegates to a Lore Cloud server. */
export class RemoteStore implements Store {
  private readonly apiUrl: string;
  private readonly apiKey: string;
  private readonly timeoutMs: number;

  constructor(options: RemoteStoreOptions) {
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

  async save(memory: Memory): Promise<void> {
    const payload = memoryToServerDict(memory);
    const resp = await this.request({ method: 'POST', path: '/v1/memories', body: payload });
    if (!resp.ok) {
      throw new Error(`Save failed (${resp.status}): ${await resp.text()}`);
    }
  }

  async get(memoryId: string): Promise<Memory | null> {
    const resp = await this.request({ method: 'GET', path: `/v1/memories/${memoryId}` });
    if (resp.status === 404) return null;
    if (!resp.ok) throw new Error(`Get failed (${resp.status}): ${await resp.text()}`);
    return serverResponseToMemory(await resp.json() as Record<string, unknown>);
  }

  async list(options?: ListOptions): Promise<Memory[]> {
    const params: Record<string, string> = {};
    if (options?.project != null) params.project = options.project;
    if (options?.type != null) params.type = options.type;
    if (options?.limit != null) params.limit = String(options.limit);

    const resp = await this.request({ method: 'GET', path: '/v1/memories', params });
    if (!resp.ok) throw new Error(`List failed (${resp.status}): ${await resp.text()}`);
    const data = await resp.json() as { memories: Record<string, unknown>[] };
    return data.memories.map(serverResponseToMemory);
  }

  async update(memory: Memory): Promise<boolean> {
    const payload: Record<string, unknown> = {
      content: memory.content,
      type: memory.type,
      context: memory.context,
      confidence: memory.confidence,
      tags: memory.tags,
      metadata: memory.metadata ?? {},
      upvotes: memory.upvotes,
      downvotes: memory.downvotes,
    };
    const resp = await this.request({
      method: 'PATCH',
      path: `/v1/memories/${memory.id}`,
      body: payload,
    });
    if (resp.status === 404) return false;
    if (!resp.ok) throw new Error(`Update failed (${resp.status}): ${await resp.text()}`);
    return true;
  }

  async delete(memoryId: string): Promise<boolean> {
    const resp = await this.request({ method: 'DELETE', path: `/v1/memories/${memoryId}` });
    if (resp.status === 404) return false;
    if (!resp.ok) throw new Error(`Delete failed (${resp.status}): ${await resp.text()}`);
    return true;
  }

  async count(options?: { project?: string; type?: string }): Promise<number> {
    const params: Record<string, string> = {};
    if (options?.project != null) params.project = options.project;
    if (options?.type != null) params.type = options.type;

    const resp = await this.request({ method: 'GET', path: '/v1/memories/count', params });
    if (!resp.ok) throw new Error(`Count failed (${resp.status}): ${await resp.text()}`);
    const data = await resp.json() as { count: number };
    return data.count;
  }

  async cleanupExpired(): Promise<number> {
    const resp = await this.request({ method: 'POST', path: '/v1/memories/cleanup' });
    if (!resp.ok) throw new Error(`Cleanup failed (${resp.status}): ${await resp.text()}`);
    const data = await resp.json() as { deleted: number };
    return data.deleted;
  }

  async search(
    embedding: number[],
    options?: { tags?: string[]; project?: string; limit?: number; minConfidence?: number },
  ): Promise<Array<Record<string, unknown>>> {
    const payload: Record<string, unknown> = {
      embedding,
      limit: options?.limit ?? 5,
      min_confidence: options?.minConfidence ?? 0.0,
    };
    if (options?.tags) payload.tags = options.tags;
    if (options?.project) payload.project = options.project;

    const resp = await this.request({ method: 'POST', path: '/v1/memories/search', body: payload });
    if (!resp.ok) throw new Error(`Search failed (${resp.status}): ${await resp.text()}`);
    const data = await resp.json() as { memories: Array<Record<string, unknown>> };
    return data.memories;
  }

  async upvote(memoryId: string): Promise<void> {
    const resp = await this.request({
      method: 'PATCH',
      path: `/v1/memories/${memoryId}`,
      body: { upvotes: '+1' },
    });
    if (resp.status === 404) throw new MemoryNotFoundError(memoryId);
    if (!resp.ok) throw new Error(`Upvote failed (${resp.status}): ${await resp.text()}`);
  }

  async downvote(memoryId: string): Promise<void> {
    const resp = await this.request({
      method: 'PATCH',
      path: `/v1/memories/${memoryId}`,
      body: { downvotes: '+1' },
    });
    if (resp.status === 404) throw new MemoryNotFoundError(memoryId);
    if (!resp.ok) throw new Error(`Downvote failed (${resp.status}): ${await resp.text()}`);
  }

  async close(): Promise<void> {
    // No persistent connection to close with fetch
  }
}
