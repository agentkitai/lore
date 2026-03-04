import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { SqliteStore } from '../src/store/sqlite.js';
import type { Memory } from '../src/types.js';
import { mkdtempSync, rmSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';

function makeMemoryFixture(overrides: Partial<Memory> = {}): Memory {
  return {
    id: 'test-id-1',
    content: 'test content',
    type: 'general',
    context: null,
    tags: ['tag1'],
    metadata: null,
    confidence: 0.8,
    source: 'test-agent',
    project: null,
    embedding: null,
    createdAt: '2026-01-01T00:00:00.000Z',
    updatedAt: '2026-01-01T00:00:00.000Z',
    ttl: null,
    expiresAt: null,
    upvotes: 0,
    downvotes: 0,
    ...overrides,
  };
}

describe('SqliteStore', () => {
  let store: SqliteStore;
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = mkdtempSync(join(tmpdir(), 'lore-test-'));
    store = new SqliteStore(join(tmpDir, 'test.db'));
  });

  afterEach(async () => {
    await store?.close();
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it('save and get', async () => {
    const memory = makeMemoryFixture();
    await store.save(memory);
    const got = await store.get('test-id-1');
    expect(got).not.toBeNull();
    expect(got!.content).toBe('test content');
    expect(got!.tags).toEqual(['tag1']);
    expect(got!.confidence).toBe(0.8);
  });

  it('get returns null for missing', async () => {
    expect(await store.get('nope')).toBeNull();
  });

  it('save with metadata round-trips JSON', async () => {
    await store.save(makeMemoryFixture({ metadata: { key: 'value' } }));
    const got = await store.get('test-id-1');
    expect(got!.metadata).toEqual({ key: 'value' });
  });

  it('list returns ordered by created_at desc', async () => {
    await store.save(makeMemoryFixture({ id: 'a', createdAt: '2026-01-01T00:00:00Z' }));
    await store.save(makeMemoryFixture({ id: 'b', createdAt: '2026-01-02T00:00:00Z' }));
    const all = await store.list();
    expect(all).toHaveLength(2);
    expect(all[0].id).toBe('b');
  });

  it('list filters by project', async () => {
    await store.save(makeMemoryFixture({ id: 'a', project: 'foo' }));
    await store.save(makeMemoryFixture({ id: 'b', project: 'bar' }));
    const filtered = await store.list({ project: 'foo' });
    expect(filtered).toHaveLength(1);
    expect(filtered[0].id).toBe('a');
  });

  it('list filters by type', async () => {
    await store.save(makeMemoryFixture({ id: 'a', type: 'general' }));
    await store.save(makeMemoryFixture({ id: 'b', type: 'lesson' }));
    const filtered = await store.list({ type: 'lesson' });
    expect(filtered).toHaveLength(1);
    expect(filtered[0].id).toBe('b');
  });

  it('list respects limit', async () => {
    await store.save(makeMemoryFixture({ id: 'a', createdAt: '2026-01-01T00:00:00Z' }));
    await store.save(makeMemoryFixture({ id: 'b', createdAt: '2026-01-02T00:00:00Z' }));
    const limited = await store.list({ limit: 1 });
    expect(limited).toHaveLength(1);
  });

  it('update existing memory', async () => {
    await store.save(makeMemoryFixture());
    const ok = await store.update(makeMemoryFixture({ content: 'updated' }));
    expect(ok).toBe(true);
    const got = await store.get('test-id-1');
    expect(got!.content).toBe('updated');
  });

  it('update returns false for missing', async () => {
    expect(await store.update(makeMemoryFixture({ id: 'nope' }))).toBe(false);
  });

  it('delete existing memory', async () => {
    await store.save(makeMemoryFixture());
    expect(await store.delete('test-id-1')).toBe(true);
    expect(await store.get('test-id-1')).toBeNull();
  });

  it('delete returns false for missing', async () => {
    expect(await store.delete('nope')).toBe(false);
  });

  it('save is upsert (INSERT OR REPLACE)', async () => {
    await store.save(makeMemoryFixture());
    await store.save(makeMemoryFixture({ content: 'replaced' }));
    const got = await store.get('test-id-1');
    expect(got!.content).toBe('replaced');
    const all = await store.list();
    expect(all).toHaveLength(1);
  });

  it('count returns number of memories', async () => {
    await store.save(makeMemoryFixture({ id: 'a' }));
    await store.save(makeMemoryFixture({ id: 'b' }));
    expect(await store.count()).toBe(2);
  });

  it('cleanupExpired removes expired memories', async () => {
    await store.save(makeMemoryFixture({ id: 'expired', expiresAt: '2020-01-01T00:00:00Z' }));
    await store.save(makeMemoryFixture({ id: 'valid', expiresAt: '2099-01-01T00:00:00Z' }));
    await store.save(makeMemoryFixture({ id: 'no-ttl' }));
    const deleted = await store.cleanupExpired();
    expect(deleted).toBe(1);
    expect(await store.get('expired')).toBeNull();
    expect(await store.get('valid')).not.toBeNull();
  });

  it('context field round-trips', async () => {
    await store.save(makeMemoryFixture({ context: 'production outage' }));
    const got = await store.get('test-id-1');
    expect(got!.context).toBe('production outage');
  });
});
