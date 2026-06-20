# Lore (TypeScript)

[![npm](https://img.shields.io/npm/v/lore-sdk)](https://www.npmjs.com/package/lore-sdk)
[![Tests](https://img.shields.io/github/actions/workflow/status/agentkitai/lore/ci.yml?label=tests)](https://github.com/agentkitai/lore/actions)
[![License](https://img.shields.io/github/license/agentkitai/lore)](../LICENSE)

**Cross-agent memory for TypeScript.** Agents remember what they learn, other agents recall it. PII redacted automatically.

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

await lore.remember('Stripe API returns 429 after 100 req/min', {
  type: 'lesson',
  context: 'Exponential backoff starting at 1s, cap at 32s',
  tags: ['stripe', 'rate-limit'],
});

const results = await lore.recall('stripe rate limiting');
const prompt = lore.asPrompt(results);
// Inject `prompt` into your agent's system message
```

## API Reference

### `new Lore(options?)`

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `project` | `string` | — | Scope memories to a project |
| `dbPath` | `string` | `~/.lore/default.db` | SQLite database path |
| `store` | `Store` | — | Custom storage backend |
| `embeddingFn` | `(text: string) => number[] \| Promise<number[]>` | — | Embedding function (**required for `recall()`**) |
| `redact` | `boolean` | `true` | Enable automatic PII redaction |
| `redactPatterns` | `[RegExp \| string, string][]` | — | Custom redaction patterns |
| `decayHalfLifeDays` | `number` | `30` | Score decay half-life |

### `lore.remember(content, options?): Promise<string>`

Store a memory. Returns the memory ID (ULID).

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `type` | `string` | `'general'` | Memory type (e.g. `lesson`, `note`, `fact`) |
| `context` | `string` | — | Additional context |
| `tags` | `string[]` | `[]` | Filterable tags |
| `metadata` | `Record<string, unknown>` | — | Arbitrary metadata |
| `source` | `string` | — | Source identifier |
| `project` | `string` | instance default | Override project scope |
| `ttl` | `number` | — | Time-to-live in seconds |

> **Secret blocking:** if redaction is enabled and `remember()` detects a secret, it throws `SecretBlockedError` and the memory is not stored. PII is masked automatically.

### `lore.recall(query, options?): Promise<RecallResult[]>`

Semantic search over memories. Requires `embeddingFn`.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `tags` | `string[]` | — | Filter: must have ALL tags |
| `type` | `string` | — | Filter by memory type |
| `limit` | `number` | `5` | Max results |

### `lore.asPrompt(results, maxTokens?): string`

Format recall results as markdown for system prompt injection. Default `maxTokens` is 1000.

### `lore.get(memoryId): Promise<Memory | null>`

Get a memory by ID.

### `lore.listMemories(options?): Promise<Memory[]>`

List memories. Options: `{ project?, type?, limit? }`. Excludes expired memories.

### `lore.forget(memoryId): Promise<boolean>`

Delete a memory by ID. Returns `true` if it existed.

### `lore.upvote(memoryId): Promise<void>`

Increment upvotes. Throws if not found.

### `lore.downvote(memoryId): Promise<void>`

Increment downvotes. Throws if not found.

### `lore.close(): Promise<void>`

Close the underlying store.

> **Deprecated aliases:** `publish()`, `query()`, `list()`, and `delete()` remain as thin backward-compat wrappers around `remember()`, `recall()`, `listMemories()`, and `forget()`. Prefer the current names in new code.

## Key Difference from Python SDK

The TypeScript SDK **does not ship a built-in embedding model**. You must provide an `embeddingFn` to use `recall()`. Storing works without one (memories are saved without embeddings), but semantic search requires it.

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

## Security & Redaction

The TS SDK includes **Layer 1 (L1)** pattern-based security scanning:

- **Secrets (blocked):** API keys, JWT tokens, PEM private keys, AWS secret keys, high-entropy strings
- **PII (masked):** emails, phones, IPs, credit cards

When `remember()` detects a secret, it throws `SecretBlockedError` (preventing storage). PII is automatically masked with `[REDACTED:type]` tokens.

```typescript
import { Lore, SecretBlockedError } from 'lore-sdk';

try {
  await lore.remember('my key is sk-abc123...');
} catch (e) {
  if (e instanceof SecretBlockedError) {
    console.error(e.message); // "Content blocked: api_key detected ..."
  }
}
```

Add custom patterns:

```typescript
const lore = new Lore({
  redactPatterns: [
    [/ACCT-\d{8}/, 'account_id'],
  ],
});
```

> **Note:** Layer 2 (detect-secrets entropy analysis) and Layer 3 (SpaCy NER entity masking) are **Python-only** features. The TS SDK provides L1 regex-based detection which covers the most common secret and PII patterns.

## Examples

See [`examples/`](examples/) for runnable scripts:
- [`basic-usage.ts`](examples/basic-usage.ts) — remember, recall, format
- [`custom-embeddings.ts`](examples/custom-embeddings.ts) — using OpenAI embeddings

## License

MIT
