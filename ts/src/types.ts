/**
 * A single memory stored by an agent.
 * Field names use camelCase in TS but map to snake_case in SQLite for cross-compatibility.
 */
export interface Memory {
  id: string;
  content: string;
  type: string;
  context: string | null;
  tags: string[];
  metadata: Record<string, unknown> | null;
  confidence: number;
  source: string | null;
  project: string | null;
  embedding: Buffer | null;
  createdAt: string;
  updatedAt: string;
  ttl: number | null;
  expiresAt: string | null;
  upvotes: number;
  downvotes: number;
}

/** Options for storing a new memory. */
export interface RememberOptions {
  type?: string;
  context?: string;
  tags?: string[];
  metadata?: Record<string, unknown>;
  confidence?: number;
  source?: string;
  project?: string;
  ttl?: number;
}

/** Options for recalling memories. */
export interface RecallOptions {
  tags?: string[];
  type?: string;
  limit?: number;
  minConfidence?: number;
}

/** A recall result containing a memory and its relevance score. */
export interface RecallResult {
  memory: Memory;
  score: number;
}

/** Options for listing memories. */
export interface ListOptions {
  project?: string;
  type?: string;
  limit?: number;
}

/** Aggregate statistics about stored memories. */
export interface MemoryStats {
  total: number;
  byType: Record<string, number>;
  oldest: string | null;
  newest: string | null;
  expiredCleaned: number;
}

/** A user-provided embedding function. */
export type EmbeddingFn = (text: string) => number[] | Promise<number[]>;

/** A custom redaction pattern: [regex, label]. */
export type RedactPattern = [RegExp | string, string];

// Deprecated aliases for backward compatibility
/** @deprecated Use Memory instead */
export type Lesson = Memory;
/** @deprecated Use RecallResult instead */
export type QueryResult = RecallResult;
/** @deprecated Use RememberOptions instead */
export interface PublishOptions {
  problem: string;
  resolution: string;
  context?: string;
  tags?: string[];
  confidence?: number;
  source?: string;
  project?: string;
}
/** @deprecated Use RecallOptions instead */
export type QueryOptions = RecallOptions;
