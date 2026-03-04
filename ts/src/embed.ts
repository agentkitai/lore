/**
 * Embedding utilities: serialization, cosine similarity, and decay scoring.
 *
 * Note: We do NOT bundle @xenova/transformers — the LocalEmbedder is an
 * optional add-on. The core SDK uses EmbeddingFn or no embeddings at all.
 */

const EMBEDDING_DIM = 384;

/** Serialize a float array to a Buffer (float32 LE, matching Python struct.pack). */
export function serializeEmbedding(vec: number[]): Buffer {
  const buf = Buffer.alloc(vec.length * 4);
  for (let i = 0; i < vec.length; i++) {
    buf.writeFloatLE(vec[i], i * 4);
  }
  return buf;
}

/** Deserialize a Buffer to a float array (float32 LE). */
export function deserializeEmbedding(data: Buffer): number[] {
  if (data.length % 4 !== 0) {
    throw new Error(`Invalid embedding buffer length: ${data.length} (must be multiple of 4)`);
  }
  const count = data.length / 4;
  const result: number[] = new Array(count);
  for (let i = 0; i < count; i++) {
    result[i] = data.readFloatLE(i * 4);
  }
  return result;
}

/** Cosine similarity between two vectors. Must be same length. */
export function cosineSimilarity(a: number[], b: number[]): number {
  if (a.length !== b.length) {
    throw new Error(`Vector length mismatch: ${a.length} vs ${b.length}`);
  }
  let dot = 0;
  let normA = 0;
  let normB = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    normA += a[i] * a[i];
    normB += b[i] * b[i];
  }
  const denom = Math.sqrt(normA) * Math.sqrt(normB);
  return denom < 1e-9 ? 0 : dot / denom;
}

/** Time-decay factor: 0.5^(ageDays / halfLifeDays). */
export function decayFactor(ageDays: number, halfLifeDays: number): number {
  return Math.pow(0.5, ageDays / halfLifeDays);
}

/** Vote factor: 1.0 + (upvotes - downvotes) * 0.1, clamped to min 0.1. */
export function voteFactor(upvotes: number, downvotes: number): number {
  return Math.max(1.0 + (upvotes - downvotes) * 0.1, 0.1);
}

/** Type-specific decay half-lives (days). */
export const DECAY_HALF_LIVES: Record<string, number> = {
  code: 14,
  note: 21,
  lesson: 30,
  convention: 60,
};

/**
 * Classify text as code or prose using lightweight heuristics.
 * Port of Python lore.embed.router.detect_content_type.
 */
export function detectContentType(text: string): 'code' | 'prose' {
  let indicators = 0;

  // Syntax characters at end of lines: { } ; ( )
  if (/[{};()]\s*$/m.test(text)) indicators += 2;

  // Language keywords
  const kwMatches = text.match(
    /\b(def |function |class |import |from |const |let |var |return |if |elif |else:)/g,
  );
  if (kwMatches) {
    indicators += 2;
    if (kwMatches.length >= 3) indicators += 1;
  }

  // Operator patterns common in code
  if (/=>|->|::|\.\./.test(text)) indicators += 1;

  // Indentation-heavy
  const lines = text.split('\n');
  if (lines.length > 1) {
    const indented = lines.filter(ln => ln.startsWith('  ') || ln.startsWith('\t')).length;
    if (indented / lines.length > 0.4) indicators += 1;
  }

  // Fenced code blocks
  if (/```/.test(text)) indicators += 2;

  // Chained method calls
  if (/\w+\.\w+\(/.test(text)) indicators += 1;

  return indicators >= 3 ? 'code' : 'prose';
}

export { EMBEDDING_DIM };
