// Main SDK class
export { Lore } from './lore.js';
export type { LoreOptions } from './lore.js';

// New Memory API types
export type {
  Memory,
  SearchResult,
  StoreStats,
  RememberOptions,
  RecallOptions,
  ForgetOptions,
  ListMemoriesOptions,
} from './types.js';

// Legacy types (backward compatible)
export type { Lesson, PublishOptions, ListOptions, QueryResult, QueryOptions, EmbeddingFn, RedactPattern } from './types.js';

// Stores
export type { Store } from './store/base.js';
export { MemoryStore } from './store/memory.js';
export { SqliteStore } from './store/sqlite.js';
export { RemoteStore } from './store/remote.js';
export type { RemoteStoreOptions } from './store/remote.js';
export { RemoteMemoryStore } from './remote-memory-store.js';
export type { RemoteMemoryStoreOptions } from './remote-memory-store.js';

// Errors
export { LoreConnectionError, LoreAuthError, LessonNotFoundError, MemoryNotFoundError } from './errors.js';

// Utilities
export { RedactionPipeline, redact } from './redact.js';
export { asPrompt } from './prompt.js';
export { serializeEmbedding, deserializeEmbedding, cosineSimilarity, decayFactor, voteFactor } from './embed.js';
