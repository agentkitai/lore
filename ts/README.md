<![CDATA[# Lore (TypeScript)

[![npm](https://img.shields.io/npm/v/lore-sdk)](https://www.npmjs.com/package/lore-sdk)
[![Tests](https://img.shields.io/github/actions/workflow/status/amitpaz/lore/ci.yml?label=tests)](https://github.com/amitpaz/lore/actions)
[![License](https://img.shields.io/github/license/amitpaz/lore)](../LICENSE)

**Cross-agent memory for TypeScript.** Agents publish what they learn, other agents query it. PII redacted automatically.

> This is the TypeScript SDK. For the Python SDK and project overview, see the [main README](../README.md).

## Install

```bash
npm install lore-sdk
```

Requires Node 18+.

## Quickstart

```typescript
import { Lore } from 'lore-sdk';

// You provide the embedding function (any model works)
const lore = new Lore({
  embeddingFn: (text) => yourModel.embed(text),
});

await lore.publish({
  problem: 'Stripe API returns 429 after 100 req/min',
  resolution: 'Exponential backoff starting at 1s, cap at 32s',
  tags: ['stripe', 'rate-limit'],
  confidence: 0.9,
});

const lessons = await lore.query('stripe rate limiting');
const prompt = lore.asPrompt(lessons);
// Inject `prompt` into your agent's system message
```

## API Reference

### `new Lore(options?)`

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `project` | `string` | — | Scope lessons to a project |
| `dbPath` | `string` | `~/.lore/default.db` | SQLite database path |
| `store` | `Store` | — | Custom storage backend |
| `embeddingFn` | `(text: string) => number[] \| Promise<number[]>` | — | Embedding function (**required for `query()`**) |
| `redact` | `boolean` | `true` | Enable automatic PII redaction |
| `redactPatterns` | `[RegExp \| string, string][]` | — | Custom redaction patterns |
| `decayHalfLifeDays` | `number` | `30` | Score decay half-life |

### `lore.publish(options): Promise<string>`

Publish a lesson. Returns the lesson ID.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `problem` | `string` | *required* | What went wrong |
| `resolution` | `string` | *required* | How to fix it |
| `context` | `string` | — | Additional context |
| `tags` | `string[]` | `[]` | Filterable tags |
| `confidence` | `number` | `0.5` | Confidence (0–1) |
| `source` | `string` | — | Source identifier |
| `project` | `string` | instance default | Override project scope |

### `lore.query(text, options?): Promise<QueryResult[]>`

Semantic search over lessons. Requires `embeddingFn`.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `tags` | `string[]` | — | Filter: must have ALL tags |
| `limit` | `number` | `5` | Max results |
| `minConfidence` | `number` | `0.0` | Min confidence threshold |

### `lore.asPrompt(lessons, maxTokens?): string`

Format results as markdown for system prompt injection. Default `maxTokens` is 1000.

### `lore.get(lessonId): Promise<Lesson | null>`

Get a lesson by ID.

### `lore.list(options?): Promise<Lesson[]>`

List lessons. Options: `{ project?, limit? }`.

### `lore.delete(lessonId): Promise<boolean>`

Delete a lesson by ID.

### `lore.upvote(lessonId): Promise<void>`

Increment upvotes. Throws if not found.

### `lore.downvote(lessonId): Promise<void>`

Increment downvotes. Throws if not found.

### `lore.close(): Promise<void>`

Close the underlying store.

## Key Difference from Python SDK

The TypeScript SDK **does not ship a built-in embedding model**. You must provide an `embeddingFn` to use `query()`. Publishing works without one (lessons are stored without embeddings), but semantic search requires it.

Example with OpenAI:

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

## Redaction

Same as Python — automatic redaction of API keys, emails, phones, IPs, and credit cards. Add custom patterns:

```typescript
const lore = new Lore({
  redactPatterns: [
    [/ACCT-\d{8}/, 'account_id'],
  ],
});
```

## Examples

See [`examples/`](examples/) for runnable scripts:
- [`basic-usage.ts`](examples/basic-usage.ts) — publish, query, format
- [`custom-embeddings.ts`](examples/custom-embeddings.ts) — using OpenAI embeddings

## License

MIT
]]>