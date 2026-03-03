# TypeScript SDK Guide

## Install

```bash
npm install lore-sdk
```

## Quick Start

### Remote mode (recommended)

```typescript
import { Lore } from 'lore-sdk';

const lore = new Lore({
  store: 'remote',
  apiUrl: 'http://localhost:8765',
  apiKey: 'lore_sk_...',
});

// Store
const id = await lore.remember({
  content: 'Stripe rate-limits at 100 req/min',
  type: 'lesson',
  tags: ['stripe', 'api'],
});

// Search
const results = await lore.recall({ query: 'rate limiting' });

// List
const { memories, total } = await lore.listMemories({ limit: 10 });

// Stats
const stats = await lore.stats();

// Delete
await lore.forget({ id });

await lore.close();
```

### Local mode (SQLite)

```typescript
import { Lore } from 'lore-sdk';

const lore = new Lore({
  project: 'my-project',
  embeddingFn: async (text) => {
    // Your embedding function
    return yourModel.embed(text);
  },
});
```

## API Reference

### `new Lore(options?)`

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `store` | `'remote'` or `Store` | SQLite | Storage backend |
| `apiUrl` | `string` | -- | Server URL (remote mode) |
| `apiKey` | `string` | -- | API key (remote mode) |
| `project` | `string` | -- | Default project scope |
| `dbPath` | `string` | `~/.lore/default.db` | SQLite path (local mode) |
| `embeddingFn` | `EmbeddingFn` | -- | Embedding function (local mode) |
| `redact` | `boolean` | `true` | Enable PII redaction |

### `lore.remember(opts): Promise<string>`

```typescript
const id = await lore.remember({
  content: 'Memory content',     // required
  type: 'lesson',                // default: 'note'
  tags: ['tag1', 'tag2'],
  metadata: { key: 'value' },
  project: 'my-project',
  source: 'code-review',
  ttl: '7d',                     // expires in 7 days
});
```

### `lore.recall(opts): Promise<SearchResult[]>`

```typescript
const results = await lore.recall({
  query: 'rate limiting',        // required
  type: 'lesson',                // filter by type
  tags: ['api'],                 // filter by tags
  project: 'backend',            // filter by project
  limit: 10,                     // max results (default: 5)
});

for (const { memory, score } of results) {
  console.log(`[${score.toFixed(2)}] ${memory.content}`);
}
```

### `lore.forget(opts): Promise<number>`

```typescript
// Delete by ID
await lore.forget({ id: '01HXYZ...' });

// Bulk delete by filter
const count = await lore.forget({ tags: ['outdated'] });
```

### `lore.listMemories(opts?): Promise<{ memories, total }>`

```typescript
const { memories, total } = await lore.listMemories({
  type: 'lesson',
  tags: ['api'],
  project: 'backend',
  limit: 20,
  offset: 0,
  includeExpired: false,
});
```

### `lore.stats(project?): Promise<StoreStats>`

```typescript
const stats = await lore.stats();
console.log(`Total: ${stats.totalCount}`);
console.log(`By type:`, stats.countByType);
```

### `lore.getMemory(id): Promise<Memory | null>`

```typescript
const memory = await lore.getMemory('01HXYZ...');
```

## Types

```typescript
import type { Memory, SearchResult, StoreStats } from 'lore-sdk';
```

See the [TypeScript SDK README](../../ts/README.md) for full type definitions.

## Using with OpenAI Embeddings (local mode)

```typescript
import OpenAI from 'openai';
import { Lore } from 'lore-sdk';

const openai = new OpenAI();

const lore = new Lore({
  embeddingFn: async (text) => {
    const res = await openai.embeddings.create({
      model: 'text-embedding-3-small',
      input: text,
    });
    return res.data[0].embedding;
  },
});
```

## Error Handling

```typescript
import { LoreConnectionError, LoreAuthError, MemoryNotFoundError } from 'lore-sdk';

try {
  await lore.recall({ query: 'test' });
} catch (err) {
  if (err instanceof LoreConnectionError) {
    console.error('Cannot connect to server');
  } else if (err instanceof LoreAuthError) {
    console.error('Invalid API key');
  }
}
```
