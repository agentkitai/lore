import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { Lore } from '../src/lore.js';
import { MemoryStore } from '../src/store/memory.js';
import { SqliteStore } from '../src/store/sqlite.js';
import { mkdtempSync, rmSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';

describe('Lore with MemoryStore', () => {
  let lore: Lore;

  beforeEach(() => {
    lore = new Lore({ store: new MemoryStore() });
  });

  afterEach(async () => {
    await lore.close();
  });

  it('remember returns a ULID', async () => {
    const id = await lore.remember('test content');
    expect(id).toMatch(/^[0-9A-Z]{26}$/);
  });

  it('remember and get round-trip', async () => {
    const id = await lore.remember('rate limit fix: use backoff', {
      tags: ['api'],
      confidence: 0.9,
    });
    const memory = await lore.get(id);
    expect(memory).not.toBeNull();
    expect(memory!.content).toBe('rate limit fix: use backoff');
    expect(memory!.tags).toEqual(['api']);
    expect(memory!.confidence).toBe(0.9);
  });

  it('get returns null for missing', async () => {
    expect(await lore.get('nope')).toBeNull();
  });

  it('listMemories returns memories', async () => {
    await lore.remember('memory 1');
    await lore.remember('memory 2');
    const all = await lore.listMemories();
    expect(all).toHaveLength(2);
  });

  it('listMemories filters by project', async () => {
    await lore.remember('m1', { project: 'a' });
    await lore.remember('m2', { project: 'b' });
    const filtered = await lore.listMemories({ project: 'a' });
    expect(filtered).toHaveLength(1);
    expect(filtered[0].project).toBe('a');
  });

  it('forget removes memory', async () => {
    const id = await lore.remember('to forget');
    expect(await lore.forget(id)).toBe(true);
    expect(await lore.get(id)).toBeNull();
  });

  it('forget returns false for missing', async () => {
    expect(await lore.forget('nope')).toBe(false);
  });

  it('remember rejects invalid confidence', async () => {
    await expect(lore.remember('t', { confidence: 1.5 }))
      .rejects.toThrow(RangeError);
    await expect(lore.remember('t', { confidence: -0.1 }))
      .rejects.toThrow(RangeError);
  });

  it('remember uses project from constructor', async () => {
    const projLore = new Lore({ store: new MemoryStore(), project: 'my-proj' });
    const id = await projLore.remember('content');
    const memory = await projLore.get(id);
    expect(memory!.project).toBe('my-proj');
    await projLore.close();
  });

  it('remember option project overrides constructor project', async () => {
    const projLore = new Lore({ store: new MemoryStore(), project: 'default' });
    const id = await projLore.remember('content', { project: 'override' });
    const memory = await projLore.get(id);
    expect(memory!.project).toBe('override');
    await projLore.close();
  });

  it('remember with context', async () => {
    const id = await lore.remember('fix applied', { context: 'production outage' });
    const memory = await lore.get(id);
    expect(memory!.context).toBe('production outage');
  });

  it('remember with type', async () => {
    const id = await lore.remember('content', { type: 'lesson' });
    const memory = await lore.get(id);
    expect(memory!.type).toBe('lesson');
  });

  it('remember with metadata', async () => {
    const id = await lore.remember('content', { metadata: { key: 'value' } });
    const memory = await lore.get(id);
    expect(memory!.metadata).toEqual({ key: 'value' });
  });

  it('stats returns MemoryStats', async () => {
    await lore.remember('m1', { type: 'general' });
    await lore.remember('m2', { type: 'lesson' });
    const s = await lore.stats();
    expect(s.total).toBe(2);
    expect(s.byType['general']).toBe(1);
    expect(s.byType['lesson']).toBe(1);
    expect(s.oldest).toBeTruthy();
    expect(s.newest).toBeTruthy();
  });

  // Deprecated methods still work
  it('publish (deprecated) still works', async () => {
    const id = await lore.publish({ problem: 'p', resolution: 'r' });
    expect(id).toMatch(/^[0-9A-Z]{26}$/);
    const memory = await lore.get(id);
    expect(memory!.content).toContain('p');
    expect(memory!.content).toContain('r');
  });

  it('list (deprecated) still works', async () => {
    await lore.remember('content');
    const all = await lore.list();
    expect(all).toHaveLength(1);
  });

  it('delete (deprecated) still works', async () => {
    const id = await lore.remember('content');
    expect(await lore.delete(id)).toBe(true);
  });
});

describe('Lore with SqliteStore', () => {
  let lore: Lore;
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = mkdtempSync(join(tmpdir(), 'lore-test-'));
    lore = new Lore({ dbPath: join(tmpDir, 'test.db') });
  });

  afterEach(async () => {
    await lore?.close();
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it('full CRUD cycle', async () => {
    const id = await lore.remember('timeout: increase to 120s', {
      tags: ['api'],
      confidence: 0.85,
    });

    // Read
    const memory = await lore.get(id);
    expect(memory!.content).toBe('timeout: increase to 120s');

    // List
    const all = await lore.listMemories();
    expect(all).toHaveLength(1);

    // Delete
    expect(await lore.forget(id)).toBe(true);
    expect(await lore.listMemories()).toHaveLength(0);
  });

  it('default db path creates ~/.lore/default.db', async () => {
    const defaultLore = new Lore();
    const id = await defaultLore.remember('test content');
    expect(id).toBeTruthy();
    await defaultLore.close();
  });
});
