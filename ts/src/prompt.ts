/**
 * Prompt helper — formats recall results for system prompt injection.
 */

import type { RecallResult } from './types.js';

const HEADER = '## Relevant Memories\n';

/**
 * Format recall results into a markdown string for system prompt injection.
 *
 * @param results - Recall results from lore.recall()
 * @param maxTokens - Approximate token budget (1 token ≈ 4 chars)
 * @returns Formatted markdown string, or empty string if no results
 */
export function asPrompt(results: RecallResult[], maxTokens = 1000): string {
  if (results.length === 0) return '';

  const maxChars = maxTokens * 4;

  // Sort by score descending (should already be sorted, but be safe)
  const sorted = [...results].sort((a, b) => b.score - a.score);

  const parts: string[] = [HEADER];
  let currentLen = HEADER.length;

  for (const result of sorted) {
    const mem = result.memory;
    const block =
      `**Content:** ${mem.content}\n` +
      `**Type:** ${mem.type}\n` +
      `**Confidence:** ${mem.confidence}\n`;
    const blockLen = block.length + 1; // +1 for separator newline

    if (currentLen + blockLen > maxChars) break;

    parts.push(block);
    currentLen += blockLen;
  }

  // If no results fit, return empty
  if (parts.length === 1) return '';

  return parts.join('\n');
}
