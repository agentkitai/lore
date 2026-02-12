# Lore

[![PyPI](https://img.shields.io/pypi/v/lore-sdk)](https://pypi.org/project/lore-sdk/)
[![npm](https://img.shields.io/npm/v/lore-sdk)](https://www.npmjs.com/package/lore-sdk)
[![Tests](https://img.shields.io/github/actions/workflow/status/amitpaz1/lore/ci.yml?label=tests)](https://github.com/amitpaz1/lore/actions)
[![License](https://img.shields.io/github/license/amitpaz1/lore)](LICENSE)

**Cross-agent memory.** Agents publish what they learn, other agents query it. PII redacted automatically.

## Why Lore?

Your agents keep making the same mistakes. Agent A discovers Stripe rate-limits at 100 req/min. Agent B hits the same wall tomorrow. No learning transfer.

Lore fixes this. It's a tiny library — no server, no infra — that gives agents a shared memory of operational lessons. Publish a lesson in one line, query it in another. Sensitive data is redacted before storage automatically.

**What Lore is:** A local-first SDK for storing and retrieving structured lessons across agent runs. SQLite-backed, embedding-powered semantic search, automatic PII redaction.

**What Lore is not:** A conversation memory store (see Mem0/Zep), a vector database, or a RAG framework.

## Quickstart

```python
from lore import Lore

lore = Lore()  # zero config — local SQLite, built-in embeddings

lore.publish(
    problem="Stripe API returns 429 after 100 req/min",
    resolution="Exponential backoff starting at 1s, cap at 32s",
    tags=["stripe", "rate-limit"],
    confidence=0.9,
)

lessons = lore.query("stripe rate limiting")
prompt = lore.as_prompt(lessons)  # ready for system prompt injection
```

```typescript
import { Lore } from 'lore-sdk';

const lore = new Lore({ embeddingFn: yourEmbedFn });

await lore.publish({
  problem: 'Stripe API returns 429 after 100 req/min',
  resolution: 'Exponential backoff starting at 1s, cap at 32s',
  tags: ['stripe', 'rate-limit'],
  confidence: 0.9,
});

const lessons = await lore.query('stripe rate limiting');
const prompt = lore.asPrompt(lessons);
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
| `project` | `str \| None` | `None` | Scope lessons to a project name |
| `db_path` | `str \| None` | `~/.lore/default.db` | Path to SQLite database |
| `store` | `Store \| None` | `None` | Custom storage backend |
| `embedding_fn` | `Callable[[str], list[float]] \| None` | `None` | Custom embedding function |
| `embedder` | `Embedder \| None` | `None` | Custom embedder instance |
| `redact` | `bool` | `True` | Enable automatic PII redaction |
| `redact_patterns` | `list[tuple[str, str]] \| None` | `None` | Custom redaction patterns as `(regex, label)` |
| `decay_half_life_days` | `float` | `30` | Half-life for lesson score decay |

Lore supports context manager usage:

```python
with Lore() as lore:
    lore.publish(problem="...", resolution="...")
```

### `lore.publish(problem, resolution, context?, tags?, confidence?, source?, project?) → str`

Publish a lesson. Returns the lesson ID (ULID).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `problem` | `str` | *required* | What went wrong |
| `resolution` | `str` | *required* | How to fix it |
| `context` | `str \| None` | `None` | Additional context |
| `tags` | `list[str] \| None` | `[]` | Filterable tags |
| `confidence` | `float` | `0.5` | Confidence score (0.0–1.0) |
| `source` | `str \| None` | `None` | Who/what created this lesson |
| `project` | `str \| None` | instance default | Override project scope |

### `lore.query(text, tags?, limit?, min_confidence?) → list[QueryResult]`

Query lessons by semantic similarity.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `text` | `str` | *required* | Search query |
| `tags` | `list[str] \| None` | `None` | Filter: lessons must have ALL these tags |
| `limit` | `int` | `5` | Max results |
| `min_confidence` | `float` | `0.0` | Minimum confidence threshold |

Returns `list[QueryResult]` sorted by score (cosine similarity × confidence × time decay × vote factor).

### `lore.as_prompt(lessons, max_tokens?) → str`

Format query results as a markdown string for system prompt injection.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `lessons` | `list[QueryResult]` | *required* | Results from `query()` |
| `max_tokens` | `int` | `1000` | Approximate token budget (1 token ≈ 4 chars) |

### `lore.get(lesson_id) → Lesson | None`

Retrieve a single lesson by ID.

### `lore.list(project?, limit?) → list[Lesson]`

List lessons, optionally filtered by project.

### `lore.delete(lesson_id) → bool`

Delete a lesson. Returns `True` if found and deleted.

### `lore.upvote(lesson_id) → None`

Increment a lesson's upvote count. Raises `LessonNotFoundError` if not found.

### `lore.downvote(lesson_id) → None`

Increment a lesson's downvote count. Raises `LessonNotFoundError` if not found.

### `lore.export_lessons(path?) → list[dict]`

Export lessons as JSON-serializable dicts. If `path` is given, writes to file.

### `lore.import_lessons(path?, data?) → int`

Import lessons from file or data. Skips duplicates by ID. Returns count imported.

### `lore.close() → None`

Close the underlying store.

## TypeScript API Reference

The TypeScript SDK mirrors the Python API. See [ts/README.md](ts/README.md) for full details.

Key differences:
- All store operations are `async`
- Constructor takes an options object: `new Lore({ project, dbPath, embeddingFn, ... })`
- No built-in embedding model — you must provide `embeddingFn`
- `asPrompt()` instead of `as_prompt()`
- `minConfidence` instead of `min_confidence` (camelCase throughout)

## Redaction

Lore automatically redacts sensitive data before storage:

- **API keys** (Bearer tokens, `sk-*`, `key-*`, etc.)
- **Email addresses**
- **Phone numbers**
- **IP addresses** (IPv4 and IPv6)
- **Credit card numbers** (with Luhn validation)

```python
lore.publish(
    problem="Auth failed with key sk-abc123def456ghi789jkl012mno",
    resolution="Rotate the key",
)
# Stored as: "Auth failed with key [REDACTED:api_key]"
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

- **Time decay:** Lessons lose relevance over time (configurable half-life, default 30 days)
- **Vote factor:** `1.0 + (upvotes - downvotes) × 0.1`, floored at 0.1
- **Confidence:** Author's self-assessed confidence (0.0–1.0)

## Remote Server (Lore Cloud)

Share lessons across agents, machines, and teams with the Lore Cloud server.

### 5-Line Remote Setup

```python
from lore import Lore

lore = Lore(store="remote", api_url="http://localhost:8765", api_key="lore_sk_...")
lore.publish(problem="Docker builds fail on M1", resolution="Use --platform linux/amd64")
lessons = lore.query("Docker build issues")
```

### Self-Host with Docker Compose

```bash
docker compose -f docker-compose.prod.yml up -d
curl -X POST http://localhost:8765/v1/org/init \
  -H "Content-Type: application/json" -d '{"name": "my-org"}'
```

→ [Self-Hosted Guide](docs/self-hosted.md) · [API Reference](docs/api-reference.md)

### MCP Integration (Claude Desktop / OpenClaw)

Give Claude direct access to your lesson memory:

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

## License

MIT
