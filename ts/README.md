# lore-sdk (TypeScript)

[![npm](https://img.shields.io/npm/v/lore-sdk)](https://www.npmjs.com/package/lore-sdk)
[![License](https://img.shields.io/github/license/amitpaz1/lore)](../LICENSE)

**Universal AI memory layer — TypeScript SDK.** Give your AI agents persistent memory across conversations.

> This is the TypeScript SDK. For the Python SDK and project overview, see the [main README](../README.md).

## Install

```bash
npm install lore-sdk
```

Requires Node 18+.

## Quick Start

### Remote mode (recommended — connects to Lore server)

```typescript
import { Lore } from 'lore-sdk';

const lore = new Lore({
  store: 'remote',
  apiUrl: 'http://localhost:8765',
  apiKey: 'lore_sk_...',
});

// Store a memory
const id = await lore.remember({
  content: 'Stripe rate-limits at 100 req/min. Use exponential backoff.',
  type: 'lesson',
  tags: ['stripe', 'rate-limit'],
});

// Search memories
const results = await lore.recall({ query: 'rate limiting', limit: 5 });
for (const { memory, score } of results) {
  console.log(`[${score.toFixed(2)}] ${memory.content}`);
}

// List memories
const { memories, total } = await lore.listMemories({ type: 'lesson', limit: 10 });

// Get statistics
const stats = await lore.stats();
console.log(`Total memories: ${stats.totalCount}`);

// Delete a memory
await lore.forget({ id });

await lore.close();
```

### Local mode (SQLite, requires embedding function)

```typescript
import { Lore } from 'lore-sdk';

const lore = new Lore({
  project: 'my-project',
  embeddingFn: async (text) => {
    // Your embedding function (e.g., OpenAI, local model)
    return new Array(384).fill(0); // placeholder
  },
});

await lore.remember({ content: 'My first memory' });
await lore.close();
```

## Memory API

### `lore.remember(opts): Promise<string>`

Store a memory. Returns the memory ID.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `content` | `string` | *required* | Memory content |
| `type` | `string` | `'note'` | Memory type (note, lesson, snippet, etc.) |
| `tags` | `string[]` | `[]` | Filterable tags |
| `metadata` | `object` | `{}` | Arbitrary metadata |
| `project` | `string` | instance default | Project scope |
| `source` | `string` | — | Source identifier |
| `ttl` | `string` | — | Time to live (`'7d'`, `'1h'`, `'30m'`) |

### `lore.recall(opts): Promise<SearchResult[]>`

Semantic search over memories.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `query` | `string` | *required* | Search text |
| `type` | `string` | — | Filter by type |
| `tags` | `string[]` | — | Filter: must have ALL tags |
| `project` | `string` | instance default | Filter by project |
| `limit` | `number` | `5` | Max results |

### `lore.forget(opts): Promise<number>`

Delete memories. Returns count deleted.

| Option | Type | Description |
|--------|------|-------------|
| `id` | `string` | Delete single memory by ID |
| `type` | `string` | Bulk delete by type |
| `tags` | `string[]` | Bulk delete by tags |
| `project` | `string` | Bulk delete by project |

### `lore.listMemories(opts?): Promise<{ memories, total }>`

List memories with pagination and filters.

### `lore.stats(project?): Promise<StoreStats>`

Get memory store statistics.

### `lore.getMemory(id): Promise<Memory | null>`

Get a single memory by ID.

## Key Difference from Python SDK

In **remote mode** (recommended), the server handles embedding — no local model needed.

In **local mode**, the TypeScript SDK does not ship a built-in embedding model. Provide an `embeddingFn` for semantic search. Example with OpenAI:

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

## Legacy API

The `publish()` / `query()` / `upvote()` / `downvote()` methods still work for backward compatibility but are deprecated. Use `remember()` / `recall()` / `forget()` instead.

## License

MIT