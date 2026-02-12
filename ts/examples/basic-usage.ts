/**
 * Basic Lore usage — publish, query, and format lessons.
 *
 * Note: This example uses a fake embedding function for demonstration.
 * Replace with a real embedding model (OpenAI, Cohere, etc.) in production.
 */

import { Lore } from 'lore-sdk';
import { createHash } from 'crypto';

// Simple deterministic embedding for demonstration
function fakeEmbedding(text: string): number[] {
  const hash = createHash('sha256').update(text).digest();
  return Array.from({ length: 384 }, (_, i) => (hash[i % 32] - 128) / 128);
}

async function main() {
  const lore = new Lore({
    dbPath: '/tmp/lore_ts_example.db',
    embeddingFn: fakeEmbedding,
  });

  // Publish lessons
  await lore.publish({
    problem: 'Stripe API returns 429 after 100 requests/min',
    resolution: 'Add exponential backoff starting at 1s, cap at 32s',
    tags: ['stripe', 'rate-limit'],
    confidence: 0.9,
  });

  await lore.publish({
    problem: 'OpenAI API times out on large prompts',
    resolution: 'Split into chunks of 50K tokens',
    tags: ['openai', 'timeout'],
    confidence: 0.8,
  });

  // Query
  const results = await lore.query('how to handle API rate limits');
  console.log(`Found ${results.length} results:\n`);

  for (const r of results) {
    console.log(`  [${r.score.toFixed(3)}] ${r.lesson.problem}`);
    console.log(`           → ${r.lesson.resolution}\n`);
  }

  // Format for prompt
  const prompt = lore.asPrompt(results);
  console.log('--- Prompt section ---');
  console.log(prompt);

  await lore.close();
}

main().catch(console.error);
