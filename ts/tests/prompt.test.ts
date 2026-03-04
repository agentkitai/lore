import { describe, it, expect } from 'vitest';
import { asPrompt } from '../src/prompt.js';
import type { RecallResult, Memory } from '../src/types.js';

function makeResult(content: string, confidence: number, score: number): RecallResult {
  const memory: Memory = {
    id: 'test-id',
    content,
    type: 'general',
    context: null,
    tags: [],
    metadata: null,
    confidence,
    source: null,
    project: null,
    embedding: null,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    ttl: null,
    expiresAt: null,
    upvotes: 0,
    downvotes: 0,
  };
  return { memory, score };
}

describe('asPrompt', () => {
  it('returns empty string for empty array', () => {
    expect(asPrompt([])).toBe('');
  });

  it('formats a single memory', () => {
    const results = [makeResult('rate limit: use backoff', 0.9, 0.8)];
    const prompt = asPrompt(results);
    expect(prompt).toContain('## Relevant Memories');
    expect(prompt).toContain('**Content:** rate limit: use backoff');
    expect(prompt).toContain('**Confidence:** 0.9');
  });

  it('sorts by score descending', () => {
    const results = [
      makeResult('low priority', 0.5, 0.3),
      makeResult('high priority', 0.9, 0.9),
    ];
    const prompt = asPrompt(results);
    const highIdx = prompt.indexOf('high');
    const lowIdx = prompt.indexOf('low');
    expect(highIdx).toBeLessThan(lowIdx);
  });

  it('truncates to maxTokens', () => {
    const results = Array.from({ length: 100 }, (_, i) =>
      makeResult(`problem ${i} ${'x'.repeat(50)}`, 0.5, 1 - i * 0.01),
    );
    const prompt = asPrompt(results, 100); // ~400 chars
    expect(prompt.length).toBeLessThan(500);
    expect(prompt).toContain('## Relevant Memories');
  });

  it('returns empty if no memories fit', () => {
    const results = [makeResult('x'.repeat(1000), 0.5, 0.8)];
    const prompt = asPrompt(results, 10); // ~40 chars budget
    expect(prompt).toBe('');
  });
});
