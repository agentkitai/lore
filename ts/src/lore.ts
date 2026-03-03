import { homedir } from 'os';
import { join } from 'path';
import { ulid } from 'ulid';
import type { Store } from './store/base.js';
import { SqliteStore } from './store/sqlite.js';
import { RemoteStore } from './store/remote.js';
import { RemoteMemoryStore } from './remote-memory-store.js';
import type {
  Lesson,
  Memory,
  PublishOptions,
  ListOptions,
  QueryResult,
  QueryOptions,
  SearchResult,
  StoreStats,
  RememberOptions,
  RecallOptions,
  ForgetOptions,
  ListMemoriesOptions,
  EmbeddingFn,
  RedactPattern,
} from './types.js';
import {
  serializeEmbedding,
  deserializeEmbedding,
  cosineSimilarity,
  decayFactor,
  voteFactor,
} from './embed.js';
import { RedactionPipeline } from './redact.js';
import { asPrompt as _asPrompt } from './prompt.js';

const DEFAULT_HALF_LIFE_DAYS = 30;

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
}

function utcNowIso(): string {
  return new Date().toISOString();
}

function parseTtl(ttl: string): string | null {
  const match = ttl.trim().toLowerCase().match(/^(\d+)([smhdw])$/);
  if (!match) return null;
  const value = parseInt(match[1], 10);
  const unit = match[2];
  const msMap: Record<string, number> = {
    s: 1000,
    m: 60 * 1000,
    h: 60 * 60 * 1000,
    d: 24 * 60 * 60 * 1000,
    w: 7 * 24 * 60 * 60 * 1000,
  };
  const expires = new Date(Date.now() + value * msMap[unit]);
  return expires.toISOString();
}

/**
 * Main entry point for the Lore SDK.
 *
 * Supports both the legacy Lesson API (publish/query) and the new
 * Memory API (remember/recall/forget/listMemories/stats).
 */
export class Lore {
  private store: Store;
  private memoryStore: RemoteMemoryStore | null;
  private project: string | undefined;
  private embeddingFn: EmbeddingFn | undefined;
  private redactor: RedactionPipeline | null;
  private halfLifeDays: number;

  constructor(options?: LoreOptions) {
    this.project = options?.project;
    this.halfLifeDays = options?.decayHalfLifeDays ?? DEFAULT_HALF_LIFE_DAYS;

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
      this.memoryStore = new RemoteMemoryStore({
        apiUrl: options.apiUrl,
        apiKey: options.apiKey,
      });
    } else if (options?.store && typeof options.store !== 'string') {
      this.store = options.store;
      this.memoryStore = null;
    } else {
      const dbPath = options?.dbPath ?? join(homedir(), '.lore', 'default.db');
      this.store = new SqliteStore(dbPath);
      this.memoryStore = null;
    }
  }

  // ------------------------------------------------------------------
  // New Memory API (remember / recall / forget / listMemories / stats)
  // ------------------------------------------------------------------

  /** Store a memory. Returns the memory ID. */
  async remember(opts: RememberOptions): Promise<string> {
    let content = opts.content;

    if (this.redactor) {
      content = this.redactor.run(content);
    }

    const expiresAt = opts.ttl ? parseTtl(opts.ttl) : null;
    const effectiveProject = opts.project ?? this.project ?? null;

    // Remote mode: delegate to server (server handles embedding)
    if (this.memoryStore) {
      return this.memoryStore.save({
        content,
        type: opts.type,
        tags: opts.tags,
        metadata: opts.metadata,
        source: opts.source,
        project: effectiveProject ?? undefined,
        expiresAt: expiresAt ?? undefined,
      });
    }

    // Local mode: store in SQLite lesson store with embedding
    let embeddingBuf: Buffer | null = null;
    if (this.embeddingFn) {
      const vec = await this.embeddingFn(content);
      embeddingBuf = serializeEmbedding(vec);
    }

    const now = utcNowIso();
    const lesson: Lesson = {
      id: ulid(),
      problem: content,
      resolution: '',
      context: null,
      tags: opts.tags ?? [],
      confidence: 1.0,
      source: opts.source ?? null,
      project: effectiveProject,
      embedding: embeddingBuf,
      createdAt: now,
      updatedAt: now,
      expiresAt: expiresAt,
      upvotes: 0,
      downvotes: 0,
      meta: {
        type: opts.type ?? 'note',
        ...(opts.metadata ?? {}),
      },
    };

    await this.store.save(lesson);
    return lesson.id;
  }

  /** Semantic search over memories. Returns results sorted by score. */
  async recall(opts: RecallOptions): Promise<SearchResult[]> {
    const effectiveProject = opts.project ?? this.project ?? undefined;

    // Remote mode: delegate to server
    if (this.memoryStore) {
      return this.memoryStore.search({
        query: opts.query,
        type: opts.type,
        tags: opts.tags,
        project: effectiveProject,
        limit: opts.limit,
      });
    }

    // Local mode: use legacy query
    const results = await this.query(opts.query, {
      tags: opts.tags,
      limit: opts.limit,
    });
    return results.map((r) => ({
      memory: lessonToMemory(r.lesson),
      score: r.score,
    }));
  }

  /** Delete memories. By ID (returns 1/0), or by filter (returns count). */
  async forget(opts: ForgetOptions): Promise<number> {
    // Remote mode
    if (this.memoryStore) {
      if (opts.id) {
        return (await this.memoryStore.delete(opts.id)) ? 1 : 0;
      }
      return this.memoryStore.deleteByFilter({
        type: opts.type,
        tags: opts.tags,
        project: opts.project,
      });
    }

    // Local mode
    if (opts.id) {
      return (await this.store.delete(opts.id)) ? 1 : 0;
    }

    // Local bulk delete not fully supported — delete by listing and filtering
    const all = await this.store.list({ project: opts.project });
    let count = 0;
    for (const lesson of all) {
      const typeMatch = !opts.type || (lesson.meta as Record<string, unknown> | null)?.type === opts.type;
      const tagMatch = !opts.tags || opts.tags.every((t) => lesson.tags.includes(t));
      if (typeMatch && tagMatch) {
        if (await this.store.delete(lesson.id)) count++;
      }
    }
    return count;
  }

  /** List memories with optional filters. Returns { memories, total }. */
  async listMemories(opts?: ListMemoriesOptions): Promise<{ memories: Memory[]; total: number }> {
    const effectiveProject = opts?.project ?? this.project ?? undefined;

    // Remote mode
    if (this.memoryStore) {
      return this.memoryStore.list({
        ...opts,
        project: effectiveProject,
      });
    }

    // Local mode: list all lessons (don't pass limit — we paginate ourselves)
    const lessons = await this.store.list({
      project: effectiveProject,
    });
    const now = new Date();
    let filtered = opts?.includeExpired
      ? lessons
      : lessons.filter((l) => !l.expiresAt || new Date(l.expiresAt) > now);

    if (opts?.type) {
      filtered = filtered.filter(
        (l) => (l.meta as Record<string, unknown> | null)?.type === opts.type,
      );
    }
    if (opts?.tags && opts.tags.length > 0) {
      const tagSet = new Set(opts.tags);
      filtered = filtered.filter((l) => [...tagSet].every((t) => l.tags.includes(t)));
    }

    const offset = opts?.offset ?? 0;
    const limit = opts?.limit ?? 20;
    const total = filtered.length;
    const paged = filtered.slice(offset, offset + limit);

    return {
      memories: paged.map(lessonToMemory),
      total,
    };
  }

  /** Get aggregate statistics for the memory store. */
  async stats(project?: string): Promise<StoreStats> {
    const effectiveProject = project ?? this.project ?? undefined;

    // Remote mode
    if (this.memoryStore) {
      return this.memoryStore.stats(effectiveProject);
    }

    // Local mode: compute from lessons
    const lessons = await this.store.list({ project: effectiveProject });
    const now = new Date();
    const active = lessons.filter(
      (l) => !l.expiresAt || new Date(l.expiresAt) > now,
    );
    const countByType: Record<string, number> = {};
    const countByProject: Record<string, number> = {};
    let oldest: string | null = null;
    let newest: string | null = null;

    for (const l of active) {
      const type = ((l.meta as Record<string, unknown> | null)?.type as string) ?? 'note';
      countByType[type] = (countByType[type] ?? 0) + 1;
      const proj = l.project ?? '(none)';
      countByProject[proj] = (countByProject[proj] ?? 0) + 1;
      if (!oldest || l.createdAt < oldest) oldest = l.createdAt;
      if (!newest || l.createdAt > newest) newest = l.createdAt;
    }

    return {
      totalCount: active.length,
      countByType,
      countByProject,
      oldestMemory: oldest,
      newestMemory: newest,
    };
  }

  /** Get a single memory by ID. */
  async getMemory(memoryId: string): Promise<Memory | null> {
    if (this.memoryStore) {
      return this.memoryStore.get(memoryId);
    }
    const lesson = await this.store.get(memoryId);
    return lesson ? lessonToMemory(lesson) : null;
  }

  // ------------------------------------------------------------------
  // Legacy Lesson API (backward compatible)
  // ------------------------------------------------------------------

  /** @deprecated Use remember() instead. Publish a new lesson. */
  async publish(opts: PublishOptions): Promise<string> {
    const confidence = opts.confidence ?? 0.5;
    if (confidence < 0 || confidence > 1) {
      throw new RangeError(`confidence must be between 0.0 and 1.0, got ${confidence}`);
    }

    let problem = opts.problem;
    let resolution = opts.resolution;
    let context = opts.context ?? null;

    if (this.redactor) {
      problem = this.redactor.run(problem);
      resolution = this.redactor.run(resolution);
      if (context) {
        context = this.redactor.run(context);
      }
    }

    let embeddingBuf: Buffer | null = null;
    if (this.embeddingFn) {
      const embedText = context
        ? `${problem} ${resolution} ${context}`
        : `${problem} ${resolution}`;
      const vec = await this.embeddingFn(embedText);
      embeddingBuf = serializeEmbedding(vec);
    }

    const now = utcNowIso();
    const lesson: Lesson = {
      id: ulid(),
      problem,
      resolution,
      context,
      tags: opts.tags ?? [],
      confidence,
      source: opts.source ?? null,
      project: opts.project ?? this.project ?? null,
      embedding: embeddingBuf,
      createdAt: now,
      updatedAt: now,
      expiresAt: null,
      upvotes: 0,
      downvotes: 0,
      meta: null,
    };

    await this.store.save(lesson);
    return lesson.id;
  }

  /** @deprecated Use recall() instead. Query lessons by semantic similarity. */
  async query(text: string, options?: QueryOptions): Promise<QueryResult[]> {
    if (!this.embeddingFn) {
      throw new Error('query() requires an embeddingFn to be configured');
    }

    const limit = options?.limit ?? 5;
    const minConfidence = options?.minConfidence ?? 0.0;
    const tags = options?.tags;
    const now = new Date();

    let candidates = await this.store.list({ project: this.project ?? undefined });

    candidates = candidates.filter((l) => {
      if (!l.expiresAt) return true;
      return new Date(l.expiresAt) > now;
    });

    if (tags && tags.length > 0) {
      const tagSet = new Set(tags);
      candidates = candidates.filter((l) =>
        [...tagSet].every((t) => l.tags.includes(t)),
      );
    }

    if (minConfidence > 0) {
      candidates = candidates.filter((l) => l.confidence >= minConfidence);
    }

    candidates = candidates.filter((l) => l.embedding !== null && l.embedding.length > 0);
    if (candidates.length === 0) return [];

    const queryVec = await this.embeddingFn(text);

    const results: QueryResult[] = [];
    for (const lesson of candidates) {
      const lessonVec = deserializeEmbedding(lesson.embedding!);
      const cosine = cosineSimilarity(queryVec, lessonVec);

      const ageDays =
        (now.getTime() - new Date(lesson.createdAt).getTime()) / (86400 * 1000);
      const timeFactor = decayFactor(ageDays, this.halfLifeDays);
      const vFactor = voteFactor(lesson.upvotes, lesson.downvotes);
      const decay = lesson.confidence * timeFactor * vFactor;
      const finalScore = cosine * decay;

      results.push({ lesson, score: finalScore });
    }

    results.sort((a, b) => b.score - a.score);
    return results.slice(0, limit);
  }

  /** Upvote a lesson. */
  async upvote(lessonId: string): Promise<void> {
    const lesson = await this.store.get(lessonId);
    if (!lesson) throw new Error(`Lesson not found: ${lessonId}`);
    lesson.upvotes += 1;
    lesson.updatedAt = utcNowIso();
    await this.store.update(lesson);
  }

  /** Downvote a lesson. */
  async downvote(lessonId: string): Promise<void> {
    const lesson = await this.store.get(lessonId);
    if (!lesson) throw new Error(`Lesson not found: ${lessonId}`);
    lesson.downvotes += 1;
    lesson.updatedAt = utcNowIso();
    await this.store.update(lesson);
  }

  /** Format query results for system prompt injection. */
  asPrompt(lessons: QueryResult[], maxTokens = 1000): string {
    return _asPrompt(lessons, maxTokens);
  }

  /** Get a lesson by ID. */
  async get(lessonId: string): Promise<Lesson | null> {
    return this.store.get(lessonId);
  }

  /** List lessons, optionally filtered by project. */
  async list(options?: ListOptions): Promise<Lesson[]> {
    return this.store.list(options);
  }

  /** Delete a lesson by ID. */
  async delete(lessonId: string): Promise<boolean> {
    return this.store.delete(lessonId);
  }

  /** Close underlying stores. */
  async close(): Promise<void> {
    await this.store.close();
    if (this.memoryStore) {
      await this.memoryStore.close();
    }
  }
}

/** Convert a legacy Lesson to a Memory. */
function lessonToMemory(lesson: Lesson): Memory {
  return {
    id: lesson.id,
    content: lesson.resolution
      ? `${lesson.problem}\n\n${lesson.resolution}`
      : lesson.problem,
    type: ((lesson.meta as Record<string, unknown> | null)?.type as string) ?? 'note',
    source: lesson.source,
    project: lesson.project,
    tags: lesson.tags,
    metadata: (lesson.meta as Record<string, unknown>) ?? {},
    embedding: lesson.embedding,
    createdAt: lesson.createdAt,
    updatedAt: lesson.updatedAt,
    expiresAt: lesson.expiresAt,
  };
}
