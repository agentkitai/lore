import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { Lore } from '../src/lore.js';
import { MemoryStore } from '../src/store/memory.js';

function makeLore(redact = false) {
  return new Lore({ store: new MemoryStore(), redact });
}

describe('Empty DB', () => {
  let lore: Lore;
  beforeEach(() => { lore = makeLore(); });
  afterEach(async () => { await lore.close(); });

  it('query returns empty', async () => {
    expect(await lore.list()).toEqual([]);
  });

  it('get nonexistent returns null', async () => {
    expect(await lore.get('nonexistent')).toBeNull();
  });

  it('delete nonexistent returns false', async () => {
    expect(await lore.delete('nonexistent')).toBe(false);
  });
});

describe('Unicode', () => {
  let lore: Lore;
  beforeEach(() => { lore = makeLore(); });
  afterEach(async () => { await lore.close(); });

  it('publishes Chinese characters', async () => {
    const id = await lore.publish({
      problem: 'API返回错误代码：429',
      resolution: '添加指数退避策略',
    });
    const lesson = await lore.get(id);
    expect(lesson!.problem).toContain('429');
  });

  it('publishes emoji', async () => {
    const id = await lore.publish({
      problem: '🔥 Server on fire 🔥',
      resolution: '🧯 Deploy fix 🚀',
    });
    const lesson = await lore.get(id);
    expect(lesson!.problem).toContain('🔥');
  });

  it('publishes mixed scripts', async () => {
    const id = await lore.publish({
      problem: 'Error in модуль for user テスト',
      resolution: 'Fix — UTF-8',
    });
    const lesson = await lore.get(id);
    expect(lesson!.problem).toContain('модуль');
    expect(lesson!.problem).toContain('テスト');
  });
});

describe('Long text', () => {
  let lore: Lore;
  beforeEach(() => { lore = makeLore(); });
  afterEach(async () => { await lore.close(); });

  it('handles 100K char problem', async () => {
    const long = 'x'.repeat(100_000);
    const id = await lore.publish({ problem: long, resolution: 'fix' });
    const lesson = await lore.get(id);
    expect(lesson!.problem.length).toBe(100_000);
  });
});

describe('Special characters', () => {
  let lore: Lore;
  beforeEach(() => { lore = makeLore(); });
  afterEach(async () => { await lore.close(); });

  it('handles SQL injection attempt', async () => {
    const id = await lore.publish({
      problem: "'; DROP TABLE lessons; --",
      resolution: 'Bobby Tables',
    });
    const lesson = await lore.get(id);
    expect(lesson!.problem).toContain('DROP TABLE');
  });

  it('handles HTML tags', async () => {
    const id = await lore.publish({
      problem: "<script>alert('xss')</script>",
      resolution: '<b>sanitize</b>',
    });
    const lesson = await lore.get(id);
    expect(lesson!.problem).toContain('<script>');
  });

  it('handles newlines and tabs', async () => {
    const id = await lore.publish({
      problem: 'Error\non\nmultiple\nlines',
      resolution: 'Fix:\n\t1. Do this',
    });
    const lesson = await lore.get(id);
    expect(lesson!.problem).toContain('\n');
  });

  it('handles tags with special chars', async () => {
    const id = await lore.publish({
      problem: 'test',
      resolution: 'test',
      tags: ['rate-limit', 'v2.0', 'c++', 'c#'],
    });
    const lesson = await lore.get(id);
    expect(lesson!.tags).toContain('c++');
    expect(lesson!.tags).toContain('c#');
  });
});

describe('Confidence boundaries', () => {
  let lore: Lore;
  beforeEach(() => { lore = makeLore(); });
  afterEach(async () => { await lore.close(); });

  it('accepts 0.0', async () => {
    const id = await lore.publish({ problem: 't', resolution: 't', confidence: 0.0 });
    const l = await lore.get(id);
    expect(l!.confidence).toBe(0);
  });

  it('accepts 1.0', async () => {
    const id = await lore.publish({ problem: 't', resolution: 't', confidence: 1.0 });
    const l = await lore.get(id);
    expect(l!.confidence).toBe(1);
  });

  it('rejects negative', async () => {
    await expect(lore.publish({ problem: 't', resolution: 't', confidence: -0.1 }))
      .rejects.toThrow();
  });

  it('rejects > 1', async () => {
    await expect(lore.publish({ problem: 't', resolution: 't', confidence: 1.1 }))
      .rejects.toThrow();
  });
});

describe('Redaction edge cases', () => {
  let lore: Lore;
  beforeEach(() => { lore = makeLore(true); });
  afterEach(async () => { await lore.close(); });

  it('handles empty strings', async () => {
    const id = await lore.publish({ problem: '', resolution: '' });
    const lesson = await lore.get(id);
    expect(lesson!.problem).toBe('');
  });

  it('redacts PII-only content', async () => {
    const id = await lore.publish({
      problem: 'test@example.com',
      resolution: 'sk-abc123def456ghi789jkl012mno',
    });
    const lesson = await lore.get(id);
    expect(lesson!.problem).not.toContain('test@example.com');
  });
});
