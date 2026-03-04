import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { RemoteStore } from '../src/store/remote.js';
import { LoreConnectionError, LoreAuthError, MemoryNotFoundError } from '../src/errors.js';
import type { Memory } from '../src/types.js';

function makeMockResponse(status: number, body?: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: () => Promise.resolve(typeof body === 'string' ? body : JSON.stringify(body ?? '')),
    json: () => Promise.resolve(body),
  } as Response;
}

function makeMemory(overrides?: Partial<Memory>): Memory {
  return {
    id: 'test-id-1',
    content: 'test content',
    type: 'general',
    context: null,
    tags: ['ts'],
    metadata: null,
    confidence: 0.8,
    source: null,
    project: 'myproject',
    embedding: null,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    ttl: null,
    expiresAt: null,
    upvotes: 0,
    downvotes: 0,
    ...overrides,
  };
}

describe('RemoteStore', () => {
  let store: RemoteStore;
  let fetchSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    store = new RemoteStore({ apiUrl: 'https://api.lore.dev/', apiKey: 'test-key' });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('save sends POST /v1/memories', async () => {
    fetchSpy.mockResolvedValue(makeMockResponse(201, { id: 'test-id-1' }));
    const memory = makeMemory();
    await store.save(memory);

    expect(fetchSpy).toHaveBeenCalledOnce();
    const [url, opts] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('https://api.lore.dev/v1/memories');
    expect(opts.method).toBe('POST');
    expect(opts.headers).toEqual({
      Authorization: 'Bearer test-key',
      'Content-Type': 'application/json',
    });
    const body = JSON.parse(opts.body as string);
    expect(body.content).toBe('test content');
    expect(body.embedding).toEqual([]);
  });

  it('get returns memory on 200', async () => {
    fetchSpy.mockResolvedValue(makeMockResponse(200, {
      id: 'abc', content: 'test', type: 'general', context: null,
      tags: [], confidence: 0.5, source: null, project: null,
      created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
      expires_at: null, upvotes: 1, downvotes: 0, metadata: null,
    }));

    const memory = await store.get('abc');
    expect(memory).not.toBeNull();
    expect(memory!.id).toBe('abc');
    expect(memory!.upvotes).toBe(1);
  });

  it('get returns null on 404', async () => {
    fetchSpy.mockResolvedValue(makeMockResponse(404, 'not found'));
    const result = await store.get('missing');
    expect(result).toBeNull();
  });

  it('list sends GET /v1/memories with params', async () => {
    fetchSpy.mockResolvedValue(makeMockResponse(200, { memories: [] }));
    await store.list({ project: 'proj', limit: 10 });

    const [url] = fetchSpy.mock.calls[0] as [string];
    expect(url).toContain('project=proj');
    expect(url).toContain('limit=10');
  });

  it('update returns false on 404', async () => {
    fetchSpy.mockResolvedValue(makeMockResponse(404));
    const result = await store.update(makeMemory());
    expect(result).toBe(false);
  });

  it('update returns true on 200', async () => {
    fetchSpy.mockResolvedValue(makeMockResponse(200, {}));
    const result = await store.update(makeMemory());
    expect(result).toBe(true);
  });

  it('delete returns false on 404', async () => {
    fetchSpy.mockResolvedValue(makeMockResponse(404));
    const result = await store.delete('missing');
    expect(result).toBe(false);
  });

  it('delete returns true on 200', async () => {
    fetchSpy.mockResolvedValue(makeMockResponse(200, {}));
    const result = await store.delete('id1');
    expect(result).toBe(true);
  });

  it('search sends POST /v1/memories/search', async () => {
    fetchSpy.mockResolvedValue(makeMockResponse(200, { memories: [{ id: 'x', score: 0.9 }] }));
    const results = await store.search([0.1, 0.2], { tags: ['a'], limit: 3 });

    const [url, opts] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/v1/memories/search');
    const body = JSON.parse(opts.body as string);
    expect(body.embedding).toEqual([0.1, 0.2]);
    expect(body.tags).toEqual(['a']);
    expect(body.limit).toBe(3);
    expect(results).toHaveLength(1);
  });

  it('upvote sends PATCH with +1', async () => {
    fetchSpy.mockResolvedValue(makeMockResponse(200, {}));
    await store.upvote('id1');

    const [, opts] = fetchSpy.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(opts.body as string);
    expect(body.upvotes).toBe('+1');
  });

  it('upvote throws MemoryNotFoundError on 404', async () => {
    fetchSpy.mockResolvedValue(makeMockResponse(404));
    await expect(store.upvote('missing')).rejects.toThrow(MemoryNotFoundError);
  });

  it('downvote sends PATCH with +1', async () => {
    fetchSpy.mockResolvedValue(makeMockResponse(200, {}));
    await store.downvote('id1');

    const [, opts] = fetchSpy.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(opts.body as string);
    expect(body.downvotes).toBe('+1');
  });

  it('downvote throws MemoryNotFoundError on 404', async () => {
    fetchSpy.mockResolvedValue(makeMockResponse(404));
    await expect(store.downvote('missing')).rejects.toThrow(MemoryNotFoundError);
  });

  it('throws LoreAuthError on 401', async () => {
    fetchSpy.mockResolvedValue(makeMockResponse(401, 'Unauthorized'));
    await expect(store.get('id')).rejects.toThrow(LoreAuthError);
  });

  it('throws LoreAuthError on 403', async () => {
    fetchSpy.mockResolvedValue(makeMockResponse(403, 'Forbidden'));
    await expect(store.get('id')).rejects.toThrow(LoreAuthError);
  });

  it('throws LoreConnectionError on network failure', async () => {
    fetchSpy.mockRejectedValue(new TypeError('fetch failed'));
    await expect(store.get('id')).rejects.toThrow(LoreConnectionError);
  });

  it('throws LoreConnectionError on timeout (AbortError)', async () => {
    const abortError = new DOMException('The operation was aborted', 'AbortError');
    fetchSpy.mockRejectedValue(abortError);
    await expect(store.get('id')).rejects.toThrow(LoreConnectionError);
  });

  it('strips trailing slashes from apiUrl', () => {
    const s = new RemoteStore({ apiUrl: 'https://api.lore.dev///', apiKey: 'k' });
    fetchSpy.mockResolvedValue(makeMockResponse(200, { memories: [] }));
    s.list();
  });

  it('count sends GET /v1/memories/count', async () => {
    fetchSpy.mockResolvedValue(makeMockResponse(200, { count: 42 }));
    const result = await store.count();
    expect(result).toBe(42);
  });

  it('cleanupExpired sends POST /v1/memories/cleanup', async () => {
    fetchSpy.mockResolvedValue(makeMockResponse(200, { deleted: 3 }));
    const result = await store.cleanupExpired();
    expect(result).toBe(3);
  });

  it('close resolves without error', async () => {
    await expect(store.close()).resolves.toBeUndefined();
  });
});

describe('Lore with remote store', () => {
  it('creates RemoteStore when store is "remote"', async () => {
    const { Lore } = await import('../src/lore.js');

    const fetchSpy = vi.fn().mockResolvedValue(makeMockResponse(200, { memories: [] }));
    vi.stubGlobal('fetch', fetchSpy);

    const lore = new Lore({ store: 'remote', apiUrl: 'https://api.test', apiKey: 'key' });
    await lore.listMemories();
    expect(fetchSpy).toHaveBeenCalled();
    await lore.close();

    vi.restoreAllMocks();
  });

  it('throws if apiUrl/apiKey missing with store "remote"', async () => {
    const { Lore } = await import('../src/lore.js');
    expect(() => new Lore({ store: 'remote' as 'remote' })).toThrow('apiUrl and apiKey are required');
  });
});
