export { Lore } from './lore.js';
export type { LoreOptions } from './lore.js';
export type {
  Memory,
  RememberOptions,
  RecallOptions,
  RecallResult,
  ListOptions,
  MemoryStats,
  EmbeddingFn,
  RedactPattern,
  // Deprecated aliases
  Lesson,
  QueryResult,
  PublishOptions,
  QueryOptions,
} from './types.js';
export type { Store } from './store/base.js';
export { MemoryStore } from './store/memory.js';
export { SqliteStore } from './store/sqlite.js';
export { RemoteStore } from './store/remote.js';
export type { RemoteStoreOptions } from './store/remote.js';
export { LoreConnectionError, LoreAuthError, MemoryNotFoundError, LessonNotFoundError } from './errors.js';
export { RedactionPipeline, redact } from './redact.js';
export { asPrompt } from './prompt.js';
export { serializeEmbedding, deserializeEmbedding, cosineSimilarity, decayFactor, voteFactor } from './embed.js';
