# Lore

[![PyPI](https://img.shields.io/pypi/v/lore-sdk)](https://pypi.org/project/lore-sdk/)
[![npm](https://img.shields.io/npm/v/lore-sdk)](https://www.npmjs.com/package/lore-sdk)
[![Tests](https://img.shields.io/github/actions/workflow/status/amitpaz1/lore/ci.yml?label=tests)](https://github.com/amitpaz1/lore/actions)
[![License](https://img.shields.io/github/license/amitpaz1/lore)](LICENSE)

**Cross-agent memory.** Agents store what they learn, other agents recall it. PII redacted automatically.

## Why Lore?

Your agents keep making the same mistakes. Agent A discovers Stripe rate-limits at 100 req/min. Agent B hits the same wall tomorrow. No learning transfer.

Lore fixes this. It's a tiny library — no server, no infra — that gives agents a shared memory. Remember a fact in one line, recall it in another. Sensitive data is redacted before storage automatically.

**What Lore is:** A local-first SDK for storing and retrieving memories across agent runs. SQLite-backed, embedding-powered semantic search, automatic PII redaction, TTL support.

**What Lore is not:** A conversation memory store (see Mem0/Zep), a vector database, or a RAG framework.

Integrates with [AgentLens](https://github.com/amitpaz1/agentlens) as an optional memory backend.

## Quickstart

```python
from lore import Lore

lore = Lore()  # zero config — local SQLite, built-in embeddings

lore.remember(
    "Stripe API returns 429 after 100 req/min — use exponential backoff starting at 1s, cap at 32s",
    tags=["stripe", "rate-limit"],
    confidence=0.9,
)

results = lore.recall("stripe rate limiting")
prompt = lore.as_prompt(results)  # ready for system prompt injection
```

```typescript
import { Lore } from 'lore-sdk';

const lore = new Lore({ embeddingFn: yourEmbedFn });

await lore.remember(
  'Stripe API returns 429 after 100 req/min — use exponential backoff starting at 1s, cap at 32s',
  { tags: ['stripe', 'rate-limit'], confidence: 0.9 },
);

const results = await lore.recall('stripe rate limiting');
const prompt = lore.asPrompt(results);
```

## Install

**Python** (3.9+):
```bash
pip install lore-sdk
```

**TypeScript** (Node 18+):
```bash
npm install lore-sdk
```

## Python API Reference

### `Lore(project?, db_path?, store?, embedding_fn?, embedder?, redact?, redact_patterns?, decay_half_life_days?)`

Create a Lore instance.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `project` | `str \| None` | `None` | Scope memories to a project name |
| `db_path` | `str \| None` | `~/.lore/default.db` | Path to SQLite database |
| `store` | `Store \| None` | `None` | Custom storage backend |
| `embedding_fn` | `Callable[[str], list[float]] \| None` | `None` | Custom embedding function |
| `embedder` | `Embedder \| None` | `None` | Custom embedder instance |
| `redact` | `bool` | `True` | Enable automatic PII redaction |
| `redact_patterns` | `list[tuple[str, str]] \| None` | `None` | Custom redaction patterns as `(regex, label)` |
| `decay_half_life_days` | `float` | `30` | Half-life for memory score decay |

Lore supports context manager usage:

```python
with Lore() as lore:
    lore.remember("important fact", tags=["tag"])
```

### `lore.remember(content, type?, context?, tags?, confidence?, source?, project?, ttl?, metadata?) → str`

Store a memory. Returns the memory ID (ULID).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `content` | `str` | *required* | The knowledge to store |
| `type` | `str` | `"general"` | Memory type (e.g. `"general"`, `"lesson"`, `"preference"`) |
| `context` | `str \| None` | `None` | Additional context |
| `tags` | `list[str] \| None` | `[]` | Filterable tags |
| `confidence` | `float` | `1.0` | Confidence score (0.0–1.0) |
| `source` | `str \| None` | `None` | Who/what created this memory |
| `project` | `str \| None` | instance default | Override project scope |
| `ttl` | `int \| None` | `None` | Time-to-live in seconds |
| `metadata` | `dict \| None` | `None` | Arbitrary metadata |

### `lore.recall(query, tags?, limit?, min_confidence?, type?) → list[RecallResult]`

Search memories by semantic similarity.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | `str` | *required* | Search query |
| `tags` | `list[str] \| None` | `None` | Filter: memories must have ALL these tags |
| `limit` | `int` | `5` | Max results |
| `min_confidence` | `float` | `0.0` | Minimum confidence threshold |
| `type` | `str \| None` | `None` | Filter by memory type |

Returns `list[RecallResult]` sorted by score (cosine similarity × confidence × time decay × vote factor).

### `lore.as_prompt(results, max_tokens?) → str`

Format recall results as a markdown string for system prompt injection.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `results` | `list[RecallResult]` | *required* | Results from `recall()` |
| `max_tokens` | `int` | `1000` | Approximate token budget (1 token ≈ 4 chars) |

### `lore.get(memory_id) → Memory | None`

Retrieve a single memory by ID.

### `lore.forget(memory_id) → bool`

Delete a memory. Returns `True` if found and deleted.

### `lore.list_memories(project?, type?, limit?) → list[Memory]`

List memories, optionally filtered by project or type. Excludes expired memories.

### `lore.stats(project?) → MemoryStats`

Return memory statistics: `total`, `by_type`, `oldest`, `newest`, `expired_cleaned`.

### `lore.upvote(memory_id) → None`

Increment a memory's upvote count. Raises `MemoryNotFoundError` if not found.

### `lore.downvote(memory_id) → None`

Increment a memory's downvote count. Raises `MemoryNotFoundError` if not found.

### `lore.close() → None`

Close the underlying store.

## TypeScript API Reference

The TypeScript SDK mirrors the Python API. See [ts/README.md](ts/README.md) for full details.

Key differences:
- All store operations are `async`
- Constructor takes an options object: `new Lore({ project, dbPath, embeddingFn, ... })`
- No built-in embedding model — you must provide `embeddingFn`
- `asPrompt()` instead of `as_prompt()`, `listMemories()` instead of `list_memories()`
- `minConfidence` instead of `min_confidence` (camelCase throughout)

## Redaction

Lore automatically redacts sensitive data before storage:

- **API keys** (Bearer tokens, `sk-*`, `key-*`, etc.)
- **Email addresses**
- **Phone numbers**
- **IP addresses** (IPv4 and IPv6)
- **Credit card numbers** (with Luhn validation)

```python
lore.remember("Auth failed with key sk-abc123def456ghi789jkl012mno — rotate the key")
# Stored as: "Auth failed with key [REDACTED:api_key] — rotate the key"
```

Add custom patterns:

```python
lore = Lore(redact_patterns=[
    (r"ACCT-\d{8}", "account_id"),
])
```

Disable redaction entirely with `redact=False`.

## Scoring

Query results are ranked by:

```
score = cosine_similarity × confidence × time_decay × vote_factor
```

- **Time decay:** Memories lose relevance over time (configurable half-life, default 30 days)
- **Vote factor:** `1.0 + (upvotes - downvotes) × 0.1`, floored at 0.1
- **Confidence:** Author's self-assessed confidence (0.0–1.0)

## Remote Server (Lore Cloud)

Share memories across agents, machines, and teams with the Lore Cloud server.

### 5-Line Remote Setup

```python
from lore import Lore

lore = Lore(store="remote", api_url="http://localhost:8765", api_key="lore_sk_...")
lore.remember("Docker builds fail on M1 — use --platform linux/amd64", tags=["docker"])
results = lore.recall("Docker build issues")
```

### Self-Host with Docker Compose

```bash
docker compose -f docker-compose.prod.yml up -d
curl -X POST http://localhost:8765/v1/org/init \
  -H "Content-Type: application/json" -d '{"name": "my-org"}'
```

→ [Self-Hosted Guide](docs/self-hosted.md) · [API Reference](docs/api-reference.md)

### MCP Integration (Claude Desktop / OpenClaw)

Give Claude direct access to your memory:

```bash
pip install lore-sdk[mcp]
```

```json
{
  "mcpServers": {
    "lore": {
      "command": "python",
      "args": ["-m", "lore.mcp.server"],
      "env": { "LORE_PROJECT": "my-project" }
    }
  }
}
```

→ [MCP Setup Guide](docs/mcp-setup.md)

## Examples

See [`examples/`](examples/) for runnable scripts:
- [`basic_usage.py`](examples/basic_usage.py) — publish, query, format
- [`custom_embeddings.py`](examples/custom_embeddings.py) — bring your own embedding function
- [`redaction_demo.py`](examples/redaction_demo.py) — see redaction in action


## 🧰 AgentKit Ecosystem

| Project | Description | |
|---------|-------------|-|
| [AgentLens](https://github.com/agentkitai/agentlens) | Observability & audit trail for AI agents | |
| **Lore** | Cross-agent memory and lesson sharing | ⬅️ you are here |
| [AgentGate](https://github.com/agentkitai/agentgate) | Human-in-the-loop approval gateway | |
| [FormBridge](https://github.com/agentkitai/formbridge) | Agent-human mixed-mode forms | |
| [AgentEval](https://github.com/agentkitai/agenteval) | Testing & evaluation framework | |
| [agentkit-mesh](https://github.com/agentkitai/agentkit-mesh) | Agent discovery & delegation | |
| [agentkit-cli](https://github.com/agentkitai/agentkit-cli) | Unified CLI orchestrator | |
| [agentkit-guardrails](https://github.com/agentkitai/agentkit-guardrails) | Reactive policy guardrails | |

## Enterprise Usage Patterns

### LoreClient — Hardened Async SDK

For production/enterprise use, `LoreClient` provides retry logic, graceful degradation, connection pooling, and optional batching:

```python
from lore import LoreClient

# Reads LORE_URL, LORE_API_KEY, LORE_ORG_ID, LORE_TIMEOUT from env
async with LoreClient() as client:
    # Save a memory — returns None if server is unreachable (never raises)
    memory_id = await client.save(
        problem="Rate limit exceeded on OpenAI API",
        resolution="Add exponential backoff with jitter",
        tags=["openai", "rate-limit"],
    )

    # Recall memories — returns [] if server is unreachable (never raises)
    results = await client.recall("how to handle rate limits", limit=5)
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LORE_URL` | `http://localhost:8765` | Lore server URL |
| `LORE_API_KEY` | *(empty)* | API key for authentication |
| `LORE_ORG_ID` | *(empty)* | Organization ID (multi-tenant) |
| `LORE_TIMEOUT` | `5` | Request timeout in seconds |

### Retry & Graceful Degradation

- **Retries:** 3 attempts with exponential backoff (0.5s → 1s → 2s) on 5xx and connection errors only
- **Graceful degradation:** `save()` returns `None` and `recall()` returns `[]` if the server is unreachable — they never raise exceptions
- **Connection pooling:** A single `httpx.AsyncClient` is reused across all calls

### Batched Saves

For high-throughput scenarios, enable batching to buffer saves and flush periodically:

```python
async with LoreClient(batch=True, batch_size=10, batch_interval=5.0) as client:
    # These are buffered and flushed every 5s or every 10 items
    await client.save(problem="...", resolution="...")
    await client.save(problem="...", resolution="...")
    # Remaining items flush automatically on close
```

### Constructor Parameters

```python
LoreClient(
    url="http://lore.internal:8765",  # or use LORE_URL env var
    api_key="sk-...",                  # or use LORE_API_KEY env var
    org_id="my-org",                   # or use LORE_ORG_ID env var
    timeout=10.0,                      # or use LORE_TIMEOUT env var
    batch=False,                       # enable batched saves
    batch_size=10,                     # flush after N buffered items
    batch_interval=5.0,                # flush every N seconds
)
```

## License

MIT
