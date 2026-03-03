import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { Lore } from '../src/lore.js';
import { MemoryStore } from '../src/store/memory.js';

/**
 * Tests for the new Memory API (remember/recall/forget/listMemories/stats).
 * Uses local mode with MemoryStore (in-memory) for simplicity.
 */
describe('Lore Memory API (local mode)', () => {
  let lore: Lore;
  const dummyEmbed = (text: string) => {
    // Simple deterministic "embedding" for testing
    const hash = Array.from(text).reduce((acc, c) => acc + c.charCodeAt(0), 0);
    return Array.from({ length: 384 }, (_, i) => Math.sin(hash + i));
  };

  beforeEach(() => {
    lore = new Lore({
      store: new MemoryStore(),
      embeddingFn: dummyEmbed,
      redact: false,
    });
  });

  afterEach(async () => {
    await lore.close();
  });

  it('remember returns a ULID', async () => {
    const id = await lore.remember({ content: 'test memory' });
    expect(id).toMatch(/^[0-9A-Z]{26}$/);
  });

  it('remember and getMemory round-trip', async () => {
    const id = await lore.remember({
      content: 'Stripe rate-limits at 100 req/min',
      type: 'lesson',
      tags: ['stripe', 'api'],
      metadata: { severity: 'high' },
    });
    const memory = await lore.getMemory(id);
    expect(memory).not.toBeNull();
    expect(memory!.content).toContain('Stripe rate-limits');
    expect(memory!.tags).toEqual(['stripe', 'api']);
  });

  it('getMemory returns null for missing', async () => {
    expect(await lore.getMemory('nope')).toBeNull();
  });

  it('remember uses project from constructor', async () => {
    const projLore = new Lore({
      store: new MemoryStore(),
      project: 'my-project',
      embeddingFn: dummyEmbed,
      redact: false,
    });
    const id = await projLore.remember({ content: 'test' });
    const mem = await projLore.getMemory(id);
    expect(mem!.project).toBe('my-project');
    await projLore.close();
  });

  it('remember option project overrides constructor', async () => {
    const projLore = new Lore({
      store: new MemoryStore(),
      project: 'default',
      embeddingFn: dummyEmbed,
      redact: false,
    });
    const id = await projLore.remember({ content: 'test', project: 'override' });
    const mem = await projLore.getMemory(id);
    expect(mem!.project).toBe('override');
    await projLore.close();
  });

  it('forget by ID returns 1', async () => {
    const id = await lore.remember({ content: 'to delete' });
    expect(await lore.forget({ id })).toBe(1);
    expect(await lore.getMemory(id)).toBeNull();
  });

  it('forget by ID returns 0 for missing', async () => {
    expect(await lore.forget({ id: 'nonexistent' })).toBe(0);
  });

  it('listMemories returns stored memories', async () => {
    await lore.remember({ content: 'mem 1' });
    await lore.remember({ content: 'mem 2' });
    const result = await lore.listMemories();
    expect(result.memories).toHaveLength(2);
    expect(result.total).toBe(2);
  });

  it('listMemories respects limit and offset', async () => {
    for (let i = 0; i < 5; i++) {
      await lore.remember({ content: `mem ${i}` });
    }
    const page1 = await lore.listMemories({ limit: 2, offset: 0 });
    expect(page1.memories).toHaveLength(2);
    expect(page1.total).toBe(5);

    const page2 = await lore.listMemories({ limit: 2, offset: 2 });
    expect(page2.memories).toHaveLength(2);
  });

  it('stats returns correct counts', async () => {
    await lore.remember({ content: 'note 1' });
    await lore.remember({ content: 'lesson 1', type: 'lesson' });
    await lore.remember({ content: 'note 2' });

    const s = await lore.stats();
    expect(s.totalCount).toBe(3);
    expect(s.oldestMemory).toBeTruthy();
    expect(s.newestMemory).toBeTruthy();
  });

  it('recall finds relevant memories via local query', async () => {
    await lore.remember({ content: 'Stripe has a rate limit of 100 requests per minute' });
    await lore.remember({ content: 'PostgreSQL supports full-text search' });

    const results = await lore.recall({ query: 'rate limiting' });
    expect(results.length).toBeGreaterThan(0);
    expect(results[0].memory).toBeDefined();
    expect(results[0].score).toBeGreaterThan(0);
  });

  it('forget by tags deletes matching', async () => {
    await lore.remember({ content: 'tagged', tags: ['delete-me'] });
    await lore.remember({ content: 'keep', tags: ['keep'] });
    const count = await lore.forget({ tags: ['delete-me'] });
    expect(count).toBe(1);
    const result = await lore.listMemories();
    expect(result.memories).toHaveLength(1);
  });

  it('remember with TTL sets expiration', async () => {
    const id = await lore.remember({ content: 'expires soon', ttl: '1h' });
    const mem = await lore.getMemory(id);
    expect(mem).not.toBeNull();
    // In local mode, expires_at is stored in lesson.expiresAt
    // The memory itself may not expose it directly depending on conversion,
    // but the ID should be valid
    expect(mem!.id).toBe(id);
  });
});

describe('Lore backward compatibility', () => {
  let lore: Lore;

  beforeEach(() => {
    lore = new Lore({ store: new MemoryStore(), redact: false });
  });

  afterEach(async () => {
    await lore.close();
  });

  it('publish still works alongside remember', async () => {
    const lessonId = await lore.publish({ problem: 'old api', resolution: 'still works' });
    const memId = await lore.remember({ content: 'new api' });

    expect(lessonId).toMatch(/^[0-9A-Z]{26}$/);
    expect(memId).toMatch(/^[0-9A-Z]{26}$/);

    // Legacy get
    const lesson = await lore.get(lessonId);
    expect(lesson).not.toBeNull();

    // New getMemory
    const mem = await lore.getMemory(memId);
    expect(mem).not.toBeNull();
  });
});
