import { homedir } from 'os';
import { join } from 'path';
import { ulid } from 'ulid';
import type { Store } from './store/base.js';
import { SqliteStore } from './store/sqlite.js';
import { RemoteStore } from './store/remote.js';
import type {
  Memory,
  RememberOptions,
  RecallOptions,
  RecallResult,
  ListOptions,
  MemoryStats,
  PublishOptions,
  EmbeddingFn,
  RedactPattern,
} from './types.js';
import {
  serializeEmbedding,
  deserializeEmbedding,
  cosineSimilarity,
  decayFactor,
  voteFactor,
  DECAY_HALF_LIVES,
} from './embed.js';
import { RedactionPipeline } from './redact.js';
import { asPrompt as _asPrompt } from './prompt.js';

const DEFAULT_HALF_LIFE_DAYS = 30;
const CLEANUP_INTERVAL_MS = 60_000;

/** Options for constructing a Lore instance. */
export interface LoreOptions {
  project?: string;
  dbPath?: string;
  store?: Store | 'remote';
  apiUrl?: string;
  apiKey?: string;
  embeddingFn?: EmbeddingFn;
  redact?: boolean;
  redactPatterns?: RedactPattern[];
  decayHalfLifeDays?: number;
  decayHalfLives?: Record<string, number>;
  decaySimilarityWeight?: number;
  decayFreshnessWeight?: number;
}

function utcNowIso(): string {
  return new Date().toISOString();
}

/**
 * Main entry point for the Lore SDK.
 */
export class Lore {
  private store: Store;
  private project: string | undefined;
  private embeddingFn: EmbeddingFn | undefined;
  private redactor: RedactionPipeline | null;
  private halfLifeDays: number;
  private halfLives: Record<string, number>;
  private similarityWeight: number;
  private freshnessWeight: number;
  private lastCleanup: number = 0;
  private lastCleanupCount: number = 0;

  constructor(options?: LoreOptions) {
    this.project = options?.project;
    this.halfLifeDays = options?.decayHalfLifeDays ?? DEFAULT_HALF_LIFE_DAYS;
    this.halfLives = { ...DECAY_HALF_LIVES, ...options?.decayHalfLives };
    this.similarityWeight = options?.decaySimilarityWeight ?? 0.7;
    this.freshnessWeight = options?.decayFreshnessWeight ?? 0.3;

    // Embedding
    this.embeddingFn = options?.embeddingFn;

    // Redaction
    const redactEnabled = options?.redact !== false;
    if (redactEnabled) {
      const customPatterns = options?.redactPatterns?.map(
        ([pat, label]) => [pat, label] as [RegExp | string, string],
      );
      this.redactor = new RedactionPipeline(customPatterns);
    } else {
      this.redactor = null;
    }

    if (options?.store === 'remote') {
      if (!options.apiUrl || !options.apiKey) {
        throw new Error('apiUrl and apiKey are required when store is "remote"');
      }
      this.store = new RemoteStore({ apiUrl: options.apiUrl, apiKey: options.apiKey });
    } else if (options?.store && typeof options.store !== 'string') {
      this.store = options.store;
    } else {
      const dbPath = options?.dbPath ?? join(homedir(), '.lore', 'default.db');
      this.store = new SqliteStore(dbPath);
    }
  }

  // ------------------------------------------------------------------
  // Core API
  // ------------------------------------------------------------------

  /**
   * Store a new memory. Returns the memory ID (ULID).
   */
  async remember(content: string, opts?: RememberOptions): Promise<string> {
    const confidence = opts?.confidence ?? 1.0;
    if (confidence < 0 || confidence > 1) {
      throw new RangeError(`confidence must be between 0.0 and 1.0, got ${confidence}`);
    }

    let processedContent = content;
    let context = opts?.context ?? null;

    if (this.redactor) {
      processedContent = this.redactor.run(processedContent);
      if (context) {
        context = this.redactor.run(context);
      }
    }

    // Compute embedding if we have an embedding function
    let embeddingBuf: Buffer | null = null;
    if (this.embeddingFn) {
      const embedText = context ? `${processedContent} ${context}` : processedContent;
      const vec = await this.embeddingFn(embedText);
      embeddingBuf = serializeEmbedding(vec);
    }

    const now = utcNowIso();

    let expiresAt: string | null = null;
    if (opts?.ttl != null) {
      expiresAt = new Date(Date.now() + opts.ttl * 1000).toISOString();
    }

    const memory: Memory = {
      id: ulid(),
      content: processedContent,
      type: opts?.type ?? 'general',
      context,
      tags: opts?.tags ?? [],
      metadata: opts?.metadata ?? null,
      confidence,
      source: opts?.source ?? null,
      project: opts?.project ?? this.project ?? null,
      embedding: embeddingBuf,
      createdAt: now,
      updatedAt: now,
      ttl: opts?.ttl ?? null,
      expiresAt,
      upvotes: 0,
      downvotes: 0,
    };

    await this.store.save(memory);
    return memory.id;
  }

  /**
   * Semantic search for memories.
   * Requires embeddingFn to be set.
   */
  async recall(query: string, options?: RecallOptions): Promise<RecallResult[]> {
    if (!this.embeddingFn) {
      throw new Error('recall() requires an embeddingFn to be configured');
    }

    await this._maybeCleanupExpired();

    const limit = options?.limit ?? 5;
    const minConfidence = options?.minConfidence ?? 0.0;
    const tags = options?.tags;
    const typeFilter = options?.type;
    const now = new Date();

    // Get all candidates
    let candidates = await this.store.list({ project: this.project ?? undefined, type: typeFilter });

    // Filter expired
    candidates = candidates.filter((m) => {
      if (!m.expiresAt) return true;
      return new Date(m.expiresAt) > now;
    });

    // Filter by tags
    if (tags && tags.length > 0) {
      const tagSet = new Set(tags);
      candidates = candidates.filter((m) =>
        [...tagSet].every((t) => m.tags.includes(t)),
      );
    }

    // Filter by min confidence
    if (minConfidence > 0) {
      candidates = candidates.filter((m) => m.confidence >= minConfidence);
    }

    // Filter to those with embeddings
    candidates = candidates.filter((m) => m.embedding !== null && m.embedding.length > 0);
    if (candidates.length === 0) return [];

    // Embed query
    const queryVec = await this.embeddingFn(query);

    // Weighted additive scoring: similarity + freshness
    const results: RecallResult[] = [];
    for (const memory of candidates) {
      const memVec = deserializeEmbedding(memory.embedding!);
      const cosine = cosineSimilarity(queryVec, memVec);

      const ageDays =
        (now.getTime() - new Date(memory.createdAt).getTime()) / (86400 * 1000);
      const halfLife = this.halfLives[memory.type] ?? this.halfLifeDays;
      const freshness = decayFactor(ageDays, halfLife);
      const vFactor = voteFactor(memory.upvotes, memory.downvotes);
      const similarity = cosine * memory.confidence * vFactor;
      const finalScore =
        this.similarityWeight * similarity + this.freshnessWeight * freshness;

      results.push({ memory, score: finalScore });
    }

    results.sort((a, b) => b.score - a.score);
    return results.slice(0, limit);
  }

  /** Delete a memory by ID. Returns True if it existed. */
  async forget(memoryId: string): Promise<boolean> {
    return this.store.delete(memoryId);
  }

  /** Get a memory by ID. */
  async get(memoryId: string): Promise<Memory | null> {
    return this.store.get(memoryId);
  }

  /** List memories with optional filters. Excludes expired memories. */
  async listMemories(options?: ListOptions): Promise<Memory[]> {
    const now = new Date();
    const memories = await this.store.list({ project: options?.project, type: options?.type });
    let filtered = memories.filter((m) => {
      if (!m.expiresAt) return true;
      return new Date(m.expiresAt) > now;
    });
    if (options?.limit != null) {
      filtered = filtered.slice(0, options.limit);
    }
    return filtered;
  }

  /** Return memory statistics. */
  async stats(options?: { project?: string }): Promise<MemoryStats> {
    const memories = await this.store.list({ project: options?.project });
    if (memories.length === 0) {
      return { total: 0, byType: {}, oldest: null, newest: null, expiredCleaned: this.lastCleanupCount };
    }

    const byType: Record<string, number> = {};
    for (const m of memories) {
      byType[m.type] = (byType[m.type] ?? 0) + 1;
    }

    return {
      total: memories.length,
      byType,
      oldest: memories[memories.length - 1].createdAt,
      newest: memories[0].createdAt,
      expiredCleaned: this.lastCleanupCount,
    };
  }

  /** Upvote a memory. */
  async upvote(memoryId: string): Promise<void> {
    const memory = await this.store.get(memoryId);
    if (!memory) throw new Error(`Memory not found: ${memoryId}`);
    memory.upvotes += 1;
    memory.updatedAt = utcNowIso();
    await this.store.update(memory);
  }

  /** Downvote a memory. */
  async downvote(memoryId: string): Promise<void> {
    const memory = await this.store.get(memoryId);
    if (!memory) throw new Error(`Memory not found: ${memoryId}`);
    memory.downvotes += 1;
    memory.updatedAt = utcNowIso();
    await this.store.update(memory);
  }

  /** Format recall results for system prompt injection. */
  asPrompt(results: RecallResult[], maxTokens = 1000): string {
    return _asPrompt(results, maxTokens);
  }

  /** Close underlying store. */
  async close(): Promise<void> {
    return this.store.close();
  }

  // ------------------------------------------------------------------
  // TTL Cleanup
  // ------------------------------------------------------------------

  private async _maybeCleanupExpired(): Promise<void> {
    const now = Date.now();
    if (now - this.lastCleanup >= CLEANUP_INTERVAL_MS) {
      this.lastCleanup = now;
      this.lastCleanupCount = await this.store.cleanupExpired();
    }
  }

  // ------------------------------------------------------------------
  // Deprecated methods (backward compat with pre-0.3 API)
  // ------------------------------------------------------------------

  /**
   * @deprecated Use remember() instead
   */
  async publish(opts: PublishOptions): Promise<string> {
    const content = `${opts.problem}\n\n${opts.resolution}`;
    return this.remember(content, {
      type: 'lesson',
      context: opts.context,
      tags: opts.tags,
      confidence: opts.confidence ?? 0.5,
      source: opts.source,
      project: opts.project,
    });
  }

  /**
   * @deprecated Use recall() instead
   */
  async query(text: string, options?: RecallOptions): Promise<RecallResult[]> {
    return this.recall(text, options);
  }

  /**
   * @deprecated Use listMemories() instead
   */
  async list(options?: ListOptions): Promise<Memory[]> {
    return this.listMemories(options);
  }

  /**
   * @deprecated Use forget() instead
   */
  async delete(memoryId: string): Promise<boolean> {
    return this.forget(memoryId);
  }
}
