/**
 * Core types for the Lore SDK.
 */

// ── Legacy lesson types (backward compatible) ──────────────────────

/** @deprecated Use Memory instead */
export interface Lesson {
  id: string;
  problem: string;
  resolution: string;
  context: string | null;
  tags: string[];
  confidence: number;
  source: string | null;
  project: string | null;
  embedding: Buffer | null;
  createdAt: string;
  updatedAt: string;
  expiresAt: string | null;
  upvotes: number;
  downvotes: number;
  meta: Record<string, unknown> | null;
}

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

/** @deprecated Use ListMemoriesOptions instead */
export interface ListOptions {
  project?: string;
  limit?: number;
}

/** @deprecated Use SearchResult instead */
export interface QueryResult {
  lesson: Lesson;
  score: number;
}

/** @deprecated Use RecallOptions instead */
export interface QueryOptions {
  tags?: string[];
  limit?: number;
  minConfidence?: number;
}

// ── General memory types ────────────────────────────────────────────

/** A single memory stored in Lore. */
export interface Memory {
  id: string;
  content: string;
  type: string;
  source: string | null;
  project: string | null;
  tags: string[];
  metadata: Record<string, unknown>;
  embedding: Buffer | null;
  createdAt: string;
  updatedAt: string;
  expiresAt: string | null;
}

/** A memory with its relevance score from a search. */
export interface SearchResult {
  memory: Memory;
  score: number;
}

/** Summary statistics about the memory store. */
export interface StoreStats {
  totalCount: number;
  countByType: Record<string, number>;
  countByProject: Record<string, number>;
  oldestMemory: string | null;
  newestMemory: string | null;
}

/** Options for storing a memory. */
export interface RememberOptions {
  content: string;
  type?: string;
  tags?: string[];
  metadata?: Record<string, unknown>;
  project?: string;
  source?: string;
  ttl?: string;
}

/** Options for recalling memories. */
export interface RecallOptions {
  query: string;
  type?: string;
  tags?: string[];
  project?: string;
  limit?: number;
}

/** Options for forgetting memories. */
export interface ForgetOptions {
  id?: string;
  type?: string;
  tags?: string[];
  project?: string;
}

/** Options for listing memories. */
export interface ListMemoriesOptions {
  type?: string;
  tags?: string[];
  project?: string;
  limit?: number;
  offset?: number;
  includeExpired?: boolean;
}

/** A user-provided embedding function. */
export type EmbeddingFn = (text: string) => number[] | Promise<number[]>;

/** A custom redaction pattern: [regex, label]. */
export type RedactPattern = [RegExp | string, string];
