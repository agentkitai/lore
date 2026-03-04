import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { Lore } from '../src/lore.js';
import { MemoryStore } from '../src/store/memory.js';
import type { EmbeddingFn } from '../src/types.js';

/**
 * Fake embedding function for deterministic testing.
 * Maps known words to different dimensions of a 4-dim vector.
 */
function fakeEmbedder(): EmbeddingFn {
  return (text: string) => {
    const lower = text.toLowerCase();
    const vec = [0, 0, 0, 0];
    if (lower.includes('rate') || lower.includes('limit') || lower.includes('throttle')) vec[0] = 1;
    if (lower.includes('timeout') || lower.includes('slow')) vec[1] = 1;
    if (lower.includes('auth') || lower.includes('token') || lower.includes('permission')) vec[2] = 1;
    if (lower.includes('database') || lower.includes('sql') || lower.includes('query')) vec[3] = 1;
    // Normalize
    const norm = Math.sqrt(vec.reduce((s, v) => s + v * v, 0));
    return norm > 0 ? vec.map((v) => v / norm) : [0.25, 0.25, 0.25, 0.25];
    };
}

describe('Lore.recall()', () => {
  let lore: Lore;

  beforeEach(async () => {
    lore = new Lore({
      store: new MemoryStore(),
      embeddingFn: fakeEmbedder(),
      redact: false,
    });

    await lore.remember('rate limit errors: add exponential backoff', { tags: ['api'], confidence: 0.9 });
    await lore.remember('timeout on large queries: increase timeout to 120s', { tags: ['api', 'database'], confidence: 0.8 });
    await lore.remember('auth token expired: refresh token before expiry', { tags: ['auth'], confidence: 0.7 });
  });

  afterEach(async () => {
    await lore.close();
  });

  it('returns results ranked by similarity', async () => {
    const results = await lore.recall('rate limiting');
    expect(results.length).toBeGreaterThan(0);
    expect(results[0].memory.content).toContain('rate limit');
    expect(results[0].score).toBeGreaterThan(0);
  });

  it('filters by tags', async () => {
    const results = await lore.recall('timeout', { tags: ['auth'] });
    expect(results.every((r) => r.memory.tags.includes('auth'))).toBe(true);
  });

  it('respects limit', async () => {
    const results = await lore.recall('api issues', { limit: 1 });
    expect(results.length).toBeLessThanOrEqual(1);
  });

  it('respects minConfidence', async () => {
    const results = await lore.recall('auth', { minConfidence: 0.8 });
    expect(results.every((r) => r.memory.confidence >= 0.8)).toBe(true);
  });

  it('throws without embeddingFn', async () => {
    const noEmbedLore = new Lore({ store: new MemoryStore(), redact: false });
    await expect(noEmbedLore.recall('test')).rejects.toThrow('embeddingFn');
    await noEmbedLore.close();
  });

  it('returns empty for no matches', async () => {
    const emptyLore = new Lore({
      store: new MemoryStore(),
      embeddingFn: fakeEmbedder(),
      redact: false,
    });
    const results = await emptyLore.recall('anything');
    expect(results).toEqual([]);
    await emptyLore.close();
  });

  // Deprecated query() still works
  it('query() (deprecated) delegates to recall()', async () => {
    const results = await lore.query('rate limiting');
    expect(results.length).toBeGreaterThan(0);
    expect(results[0].memory.content).toContain('rate limit');
  });
});

describe('Lore.upvote() / downvote()', () => {
  let lore: Lore;

  beforeEach(() => {
    lore = new Lore({ store: new MemoryStore(), redact: false });
  });

  afterEach(async () => {
    await lore.close();
  });

  it('upvote increments', async () => {
    const id = await lore.remember('test content');
    await lore.upvote(id);
    await lore.upvote(id);
    const memory = await lore.get(id);
    expect(memory!.upvotes).toBe(2);
  });

  it('downvote increments', async () => {
    const id = await lore.remember('test content');
    await lore.downvote(id);
    const memory = await lore.get(id);
    expect(memory!.downvotes).toBe(1);
  });

  it('upvote throws for missing memory', async () => {
    await expect(lore.upvote('nonexistent')).rejects.toThrow('not found');
  });
});

describe('Lore with redaction', () => {
  let lore: Lore;

  beforeEach(() => {
    lore = new Lore({ store: new MemoryStore() });
  });

  afterEach(async () => {
    await lore.close();
  });

  it('blocks secrets on remember', async () => {
    await expect(
      lore.remember('API key sk-abcdefghijklmnopqrst123 leaked'),
    ).rejects.toThrow('api_key detected');
  });

  it('masks PII on remember', async () => {
    const id = await lore.remember('Contact user@example.com for info');
    const memory = await lore.get(id);
    expect(memory!.content).toContain('[REDACTED:email]');
    expect(memory!.content).not.toContain('user@');
  });

  it('redact: false disables redaction', async () => {
    const noRedactLore = new Lore({ store: new MemoryStore(), redact: false });
    const id = await noRedactLore.remember('API key sk-abcdefghijklmnopqrst123 is here');
    const memory = await noRedactLore.get(id);
    expect(memory!.content).toContain('sk-');
    await noRedactLore.close();
  });

  it('custom redaction patterns work', async () => {
    const customLore = new Lore({
      store: new MemoryStore(),
      redactPatterns: [[/ACCT-\d+/, 'account_id']],
    });
    const id = await customLore.remember('account ACCT-12345 has error');
    const memory = await customLore.get(id);
    expect(memory!.content).toContain('[REDACTED:account_id]');
    await customLore.close();
  });
});

describe('Lore.asPrompt()', () => {
  it('delegates to prompt helper', () => {
    const lore = new Lore({ store: new MemoryStore(), redact: false });
    const result = lore.asPrompt([]);
    expect(result).toBe('');
  });
});
