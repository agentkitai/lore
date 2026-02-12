/**
 * Using Lore with OpenAI embeddings.
 *
 * Install: npm install openai lore-sdk
 *
 * Set OPENAI_API_KEY environment variable before running.
 */

import { Lore } from 'lore-sdk';

// To use with OpenAI (uncomment and install `openai`):
//
// import OpenAI from 'openai';
// const openai = new OpenAI();
//
// async function embed(text: string): Promise<number[]> {
//   const res = await openai.embeddings.create({
//     model: 'text-embedding-3-small',
//     input: text,
//   });
//   return res.data[0].embedding;
// }

// For this demo, we use a simple fake embedder
import { createHash } from 'crypto';

function fakeEmbed(text: string): number[] {
  const hash = createHash('sha256').update(text).digest();
  return Array.from({ length: 384 }, (_, i) => (hash[i % 32] - 128) / 128);
}

async function main() {
  const lore = new Lore({
    dbPath: '/tmp/lore_ts_custom.db',
    embeddingFn: fakeEmbed, // Replace with `embed` for OpenAI
  });

  await lore.publish({
    problem: 'SMS sending fails for international numbers',
    resolution: 'Use E.164 format with country code prefix',
    tags: ['sms', 'international'],
    confidence: 0.85,
  });

  const results = await lore.query('phone number formatting');
  for (const r of results) {
    console.log(`[${r.score.toFixed(3)}] ${r.lesson.problem}`);
  }

  await lore.close();
}

main().catch(console.error);
