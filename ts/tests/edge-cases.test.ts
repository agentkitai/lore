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

  it('listMemories returns empty', async () => {
    expect(await lore.listMemories()).toEqual([]);
  });

  it('get nonexistent returns null', async () => {
    expect(await lore.get('nonexistent')).toBeNull();
  });

  it('forget nonexistent returns false', async () => {
    expect(await lore.forget('nonexistent')).toBe(false);
  });
});

describe('Unicode', () => {
  let lore: Lore;
  beforeEach(() => { lore = makeLore(); });
  afterEach(async () => { await lore.close(); });

  it('remembers Chinese characters', async () => {
    const id = await lore.remember('API返回错误代码：429');
    const memory = await lore.get(id);
    expect(memory!.content).toContain('429');
  });

  it('remembers emoji', async () => {
    const id = await lore.remember('🔥 Server on fire 🔥');
    const memory = await lore.get(id);
    expect(memory!.content).toContain('🔥');
  });

  it('remembers mixed scripts', async () => {
    const id = await lore.remember('Error in модуль for user テスト');
    const memory = await lore.get(id);
    expect(memory!.content).toContain('модуль');
    expect(memory!.content).toContain('テスト');
  });
});

describe('Long text', () => {
  let lore: Lore;
  beforeEach(() => { lore = makeLore(); });
  afterEach(async () => { await lore.close(); });

  it('handles 100K char content', async () => {
    const long = 'x'.repeat(100_000);
    const id = await lore.remember(long);
    const memory = await lore.get(id);
    expect(memory!.content.length).toBe(100_000);
  });
});

describe('Special characters', () => {
  let lore: Lore;
  beforeEach(() => { lore = makeLore(); });
  afterEach(async () => { await lore.close(); });

  it('handles SQL injection attempt', async () => {
    const id = await lore.remember("'; DROP TABLE memories; --");
    const memory = await lore.get(id);
    expect(memory!.content).toContain('DROP TABLE');
  });

  it('handles HTML tags', async () => {
    const id = await lore.remember("<script>alert('xss')</script>");
    const memory = await lore.get(id);
    expect(memory!.content).toContain('<script>');
  });

  it('handles newlines and tabs', async () => {
    const id = await lore.remember('Error\non\nmultiple\nlines');
    const memory = await lore.get(id);
    expect(memory!.content).toContain('\n');
  });

  it('handles tags with special chars', async () => {
    const id = await lore.remember('test', {
      tags: ['rate-limit', 'v2.0', 'c++', 'c#'],
    });
    const memory = await lore.get(id);
    expect(memory!.tags).toContain('c++');
    expect(memory!.tags).toContain('c#');
  });
});

describe('Confidence boundaries', () => {
  let lore: Lore;
  beforeEach(() => { lore = makeLore(); });
  afterEach(async () => { await lore.close(); });

  it('accepts 0.0', async () => {
    const id = await lore.remember('t', { confidence: 0.0 });
    const m = await lore.get(id);
    expect(m!.confidence).toBe(0);
  });

  it('accepts 1.0', async () => {
    const id = await lore.remember('t', { confidence: 1.0 });
    const m = await lore.get(id);
    expect(m!.confidence).toBe(1);
  });

  it('rejects negative', async () => {
    await expect(lore.remember('t', { confidence: -0.1 }))
      .rejects.toThrow();
  });

  it('rejects > 1', async () => {
    await expect(lore.remember('t', { confidence: 1.1 }))
      .rejects.toThrow();
  });
});

describe('Redaction edge cases', () => {
  let lore: Lore;
  beforeEach(() => { lore = makeLore(true); });
  afterEach(async () => { await lore.close(); });

  it('handles empty strings', async () => {
    const id = await lore.remember('');
    const memory = await lore.get(id);
    expect(memory!.content).toBe('');
  });

  it('redacts PII-only content', async () => {
    const id = await lore.remember('test@example.com');
    const memory = await lore.get(id);
    expect(memory!.content).not.toContain('test@example.com');
  });
});
