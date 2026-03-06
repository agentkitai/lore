# API Reference

Complete reference for Lore v0.6.0: MCP tools, CLI commands, environment variables, and SDK.

---

## MCP Tools

Lore exposes 20 tools over the Model Context Protocol. Tools are grouped by category.

### Memory Management

#### remember

Save a memory -- any knowledge worth preserving across sessions.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `content` | string | yes | -- | The memory content. Should be a clear, self-contained piece of knowledge. |
| `type` | string | no | `"general"` | Memory type: `general`, `lesson`, `fact`, `preference`, `code`, `note`, `convention`, `debug`, `pattern` |
| `tier` | string | no | `"long"` | Memory tier: `working` (1h TTL), `short` (7d TTL), `long` (no expiry) |
| `tags` | list[string] | no | `[]` | Tags for filtering |
| `metadata` | object | no | `null` | Arbitrary JSON metadata |
| `source` | string | no | `null` | Source identifier |
| `project` | string | no | server default | Project namespace |
| `ttl` | integer | no | tier default | Custom time-to-live in seconds (overrides tier default) |

**Returns:** Confirmation with memory ID and tier.

**Example:**
```
Tool: remember
Input: {
  "content": "Stripe API returns 429 after 100 req/min. Use exponential backoff starting at 1s, cap at 32s.",
  "type": "lesson",
  "tags": ["stripe", "rate-limit"],
  "tier": "long"
}
Output: "Memory saved (ID: 01HXYZ..., tier: long)"
```

---

#### recall

Search for relevant memories using semantic similarity.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | yes | -- | Natural language search query |
| `tags` | list[string] | no | `null` | Filter by tags |
| `type` | string | no | `null` | Filter by memory type |
| `tier` | string | no | `null` | Filter by tier |
| `limit` | integer | no | `5` | Max results (1-20) |
| `repo_path` | string | no | `null` | Git repo path for freshness checking |
| `intent` | string | no | `null` | Filter by classification intent |
| `domain` | string | no | `null` | Filter by classification domain |
| `emotion` | string | no | `null` | Filter by classification emotion |
| `topic` | string | no | `null` | Filter by enrichment topic |
| `sentiment` | string | no | `null` | Filter by sentiment: `positive`, `negative`, `neutral` |
| `entity` | string | no | `null` | Filter by entity name |
| `category` | string | no | `null` | Filter by category |

**Returns:** Formatted list of matching memories with scores, importance, tier, tags, enrichment data, and optional staleness badges.

**Example:**
```
Tool: recall
Input: {"query": "how to handle Stripe rate limits", "limit": 3}
Output:
Found 1 relevant memory(ies):

------------------------------------------------------------
Memory 1  (importance: 0.95, score: 0.87, id: 01HXYZ..., type: lesson, tier: long)
Content: Stripe API returns 429 after 100 req/min. Use exponential backoff...
Tags:    stripe, rate-limit
```

---

#### forget

Delete a memory by its ID.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `memory_id` | string | yes | -- | Memory ID to delete |

**Returns:** Confirmation or "not found" message.

---

#### list_memories

List stored memories with optional filters.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `type` | string | no | `null` | Filter by memory type |
| `tier` | string | no | `null` | Filter by tier |
| `project` | string | no | `null` | Filter by project |
| `limit` | integer | no | `null` | Max results |

**Returns:** Formatted list with IDs, types, importance scores, and content previews.

---

#### stats

Return memory statistics.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `project` | string | no | `null` | Filter to project |

**Returns:** Total count, breakdown by type and tier, oldest/newest timestamps.

---

#### upvote_memory

Boost a memory's ranking after it proved helpful.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `memory_id` | string | yes | -- | Memory ID to upvote |

**Returns:** Confirmation message.

---

#### downvote_memory

Lower a memory's ranking after it proved unhelpful or incorrect.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `memory_id` | string | yes | -- | Memory ID to downvote |

**Returns:** Confirmation message.

---

### Knowledge and Facts

#### extract_facts

Extract structured (subject, predicate, object) facts from text without storing them. Requires LLM configuration.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `text` | string | yes | -- | Text to extract facts from |

**Returns:** Numbered list of fact triples with confidence scores.

**Example:**
```
Tool: extract_facts
Input: {"text": "React uses a virtual DOM for efficient rendering. It was created by Facebook."}
Output:
Extracted 2 fact(s):

1. (React, uses, virtual DOM) [confidence: 0.95]
2. (React, created_by, Facebook) [confidence: 0.92]
```

---

#### list_facts

List active (non-invalidated) facts from the knowledge base.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `subject` | string | no | `null` | Filter by subject |
| `limit` | integer | no | `50` | Max results |

**Returns:** Table of facts with subject, predicate, object, confidence, and source memory ID.

---

#### conflicts

Show recent fact conflicts detected during memory ingestion.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `resolution` | string | no | `null` | Filter by resolution type: `SUPERSEDE`, `MERGE`, `CONTRADICT` |
| `limit` | integer | no | `10` | Max results |

**Returns:** List of conflicts showing resolution type, old/new values, memory IDs, and reasoning.

---

### Knowledge Graph

#### graph_query

Traverse the knowledge graph from a given entity. Requires `LORE_KNOWLEDGE_GRAPH=true`.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `entity` | string | yes | -- | Entity name to start traversal from |
| `depth` | integer | no | `2` | Traversal depth (1-3) |
| `rel_types` | list[string] | no | `null` | Filter by relationship types |
| `direction` | string | no | `"both"` | Traversal direction: `outbound`, `inbound`, `both` |
| `min_weight` | float | no | `0.1` | Minimum relationship weight to traverse |

**Returns:** Connected entities and relationships with types and weights, plus a relevance score.

**Example:**
```
Tool: graph_query
Input: {"entity": "React", "depth": 2}
Output:
Graph query for 'React' (depth=2):

Found 5 entities, 4 relationships

  React --uses--> virtual DOM (weight: 0.95)
  React --created_by--> Facebook (weight: 0.92)
  React --depends_on--> JavaScript (weight: 0.88)
  Facebook --created_by--> Meta (weight: 0.75)

Relevance score: 0.87
```

---

#### entity_map

List entities in the knowledge graph. Requires `LORE_KNOWLEDGE_GRAPH=true`.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `entity_type` | string | no | `null` | Filter by type: `person`, `tool`, `project`, `concept`, `organization`, `platform`, `language`, `framework`, `service`, `other` |
| `limit` | integer | no | `50` | Max results |
| `format` | string | no | `"text"` | Output format: `text` or `json` (D3-compatible graph format) |

**Returns:** Table of entities with name, type, mention count, and aliases.

---

#### related

Find memories and entities related to a given memory or entity. Simpler interface than `graph_query`. Requires `LORE_KNOWLEDGE_GRAPH=true`.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `memory_id` | string | no | `null` | Memory ID to find relations for |
| `entity_name` | string | no | `null` | Entity name to find relations for |
| `depth` | integer | no | `1` | Traversal depth (1-3) |

At least one of `memory_id` or `entity_name` is required.

**Returns:** List of related entities, relationships, and connected memories.

---

### Intelligence Pipeline

#### classify

Classify text by intent, domain, and emotion without storing anything.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `text` | string | yes | -- | Text to classify |

**Returns:** Intent, domain, and emotion with confidence percentages.

**Example:**
```
Tool: classify
Input: {"text": "The deployment keeps failing because of a misconfigured environment variable"}
Output:
Intent: troubleshoot (87%)
Domain: devops (82%)
Emotion: frustration (74%)
```

---

#### enrich

Add LLM-extracted metadata to memories. Requires enrichment to be enabled.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `memory_id` | string | no | `null` | Specific memory to enrich |
| `all` | boolean | no | `false` | Enrich all unenriched memories |
| `project` | string | no | `null` | Filter to project (with `all=true`) |
| `force` | boolean | no | `false` | Re-enrich already enriched memories |

**Returns:** Summary of enriched/skipped/failed counts.

---

#### consolidate

Merge near-duplicate memories and summarize related clusters.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `project` | string | no | `null` | Filter to project |
| `dry_run` | boolean | no | `true` | Preview only (no changes) |
| `strategy` | string | no | `"all"` | Strategy: `deduplicate`, `summarize`, `all` |

**Returns:** Consolidation report showing groups found, memories consolidated/created, and previews.

---

### Import / Export

#### ingest

Import content from external sources with provenance tracking.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `content` | string | yes | -- | Content to ingest |
| `source` | string | no | `"mcp"` | Source adapter name (e.g., `slack`, `telegram`, `git`) |
| `user` | string | no | `null` | Source user identity |
| `channel` | string | no | `null` | Source channel or location |
| `type` | string | no | `"general"` | Memory type |
| `tags` | string | no | `null` | Comma-separated tags |
| `project` | string | no | `null` | Project namespace |

**Returns:** Confirmation with memory ID and source.

---

#### as_prompt

Export memories formatted for LLM context injection.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | yes | -- | Search query to find relevant memories |
| `format` | string | no | `"xml"` | Output format: `xml` (Claude), `chatml` (OpenAI), `markdown`, `raw` |
| `max_tokens` | integer | no | `null` | Max tokens in output |
| `limit` | integer | no | `10` | Max memories to include |
| `tags` | list[string] | no | `null` | Filter by tags |
| `type` | string | no | `null` | Filter by type |
| `include_metadata` | boolean | no | `false` | Include enrichment metadata in output |

**Returns:** Formatted block of memories optimized for the chosen LLM format.

---

#### check_freshness

Check if stored memories are still fresh against current git state.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `repo_path` | string | yes | -- | Path to git repository |
| `project` | string | no | `null` | Filter to project |

**Returns:** Freshness report with status per memory (fresh, possibly_stale, likely_stale, stale).

---

#### github_sync

Sync GitHub repository data into Lore as memories. Requires the `gh` CLI to be installed and authenticated.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `repo` | string | yes | -- | GitHub owner/repo (e.g., `octocat/Hello-World`) |
| `types` | string | no | `null` | Comma-separated types: `prs`, `issues`, `commits`, `releases` (default: all) |
| `since` | string | no | `null` | ISO-8601 date to start sync from |
| `project` | string | no | `null` | Project namespace for synced memories |

**Returns:** Sync summary with counts per type.

---

## CLI Commands

The `lore` CLI is installed with `pip install lore-sdk`.

### Memory Operations

```
lore remember <content> [--type TYPE] [--tier working|short|long] [--tags TAGS]
                        [--context CONTEXT] [--ttl SECONDS] [--source SOURCE]
                        [--confidence FLOAT] [--project PROJECT] [--metadata JSON]
```

Store a new memory. Prints the memory ID.

```
lore recall <query> [--type TYPE] [--tier working|short|long] [--tags TAGS]
                    [--limit N] [--topic TOPIC] [--sentiment positive|negative|neutral]
                    [--entity NAME] [--category CAT]
```

Search memories. Prints results with scores.

```
lore forget <id>
lore memories [--type TYPE] [--tier working|short|long] [--limit N]
lore stats
```

### Knowledge & Graph

```
lore facts [MEMORY_ID] [--subject SUBJECT] [--limit N]
lore conflicts [--resolution SUPERSEDE|MERGE|CONTRADICT] [--limit N]
lore graph <entity> [--depth N] [--direction outbound|inbound|both]
lore entities [--type ENTITY_TYPE] [--limit N]
lore relationships [--entity NAME] [--type REL_TYPE] [--limit N]
```

### Intelligence

```
lore classify <text> [--json]
lore enrich [MEMORY_ID] [--all] [--force]
lore consolidate [--dry-run] [--execute] [--strategy deduplicate|summarize|all]
```

### Import/Export

```
lore ingest <content> [--source SOURCE] [--user USER] [--channel CHANNEL]
lore prompt <query> [--format xml|chatml|markdown|raw] [--max-tokens N]
lore freshness [--repo PATH]
lore github-sync --repo OWNER/REPO [--types prs,issues,commits,releases]
```

### Server

```
lore mcp              # Start MCP server (stdio)
lore reindex [--dual] # Re-embed all memories
```

---

## Environment Variables

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `LORE_STORE` | `local` | Storage backend: `local` (SQLite) or `remote` (HTTP) |
| `LORE_PROJECT` | none | Default project scope |
| `LORE_API_URL` | none | Server URL (required for remote mode) |
| `LORE_API_KEY` | none | API key (required for remote mode) |

### LLM

| Variable | Default | Description |
|----------|---------|-------------|
| `LORE_LLM_PROVIDER` | none | Provider: `anthropic`, `openai`, `azure`, etc. |
| `LORE_LLM_MODEL` | `gpt-4o-mini` | Model for classification, extraction, consolidation |
| `LORE_LLM_API_KEY` | none | API key for the LLM provider |
| `LORE_LLM_BASE_URL` | none | Custom base URL for LLM API |

### Feature Toggles

| Variable | Default | Description |
|----------|---------|-------------|
| `LORE_ENRICHMENT_ENABLED` | `false` | Enable LLM enrichment |
| `LORE_ENRICHMENT_MODEL` | `gpt-4o-mini` | Model for enrichment |
| `LORE_CLASSIFY` | `false` | Enable classification on remember |
| `LORE_KNOWLEDGE_GRAPH` | `false` | Enable knowledge graph |
| `LORE_FACT_EXTRACTION` | `false` | Enable fact extraction |

### Knowledge Graph Tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `LORE_GRAPH_DEPTH` | `0` | Default graph depth during recall |
| `LORE_GRAPH_CONFIDENCE_THRESHOLD` | `0.5` | Min confidence for graph entities |
| `LORE_GRAPH_CO_OCCURRENCE` | `true` | Extract co-occurrence relationships |
| `LORE_GRAPH_CO_OCCURRENCE_WEIGHT` | `0.3` | Weight for co-occurrence edges |

---

## SDK Reference

### Lore class

```python
from lore import Lore
```

#### Constructor

```python
Lore(
    project: str | None = None,
    db_path: str | None = None,
    store: Store | str | None = None,
    embedding_fn: Callable[[str], list[float]] | None = None,
    embedder: Embedder | None = None,
    redact: bool = True,
    redact_patterns: list[tuple[str, str]] | None = None,
    dual_embedding: bool = False,
    api_url: str | None = None,
    api_key: str | None = None,
    importance_threshold: float = 0.05,
    classify: bool = False,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    llm_api_key: str | None = None,
    llm_base_url: str | None = None,
    enrichment: bool = False,
    enrichment_model: str = "gpt-4o-mini",
    fact_extraction: bool = False,
    fact_confidence_threshold: float = 0.3,
    knowledge_graph: bool = False,
    graph_depth: int = 0,
    graph_confidence_threshold: float = 0.5,
    graph_co_occurrence: bool = True,
    consolidation_config: dict | None = None,
    consolidation_schedule: str | None = None,
)
```

Key parameters:

| Parameter | Description |
|-----------|-------------|
| `project` | Default project namespace for all operations |
| `db_path` | Path to SQLite database (default: `~/.lore/default.db`) |
| `store` | `"remote"` for HTTP store, or a custom `Store` instance |
| `embedding_fn` | Custom embedding function `(str) -> list[float]` |
| `redact` | Enable secret redaction (default: True) |
| `enrichment` | Enable LLM enrichment on remember |
| `fact_extraction` | Enable fact extraction on remember |
| `knowledge_graph` | Enable knowledge graph updates on remember |

#### Public Methods

```python
# Store a memory. Returns memory ID (ULID string).
def remember(content, *, type="general", tier="long", tags=None,
             metadata=None, source=None, project=None,
             ttl=None, confidence=1.0) -> str

# Semantic search. Returns list of RecallResult.
def recall(query, *, tags=None, type=None, tier=None, limit=5,
           intent=None, domain=None, entity=None, topic=None,
           graph_depth=None) -> list[RecallResult]

# Delete a memory. Returns True if found.
def forget(memory_id) -> bool

# List memories with optional filters.
def list_memories(type=None, tier=None, project=None, limit=None) -> list[Memory]

# Get aggregate statistics.
def stats(project=None) -> MemoryStats

# Upvote/downvote a memory.
def upvote(memory_id) -> None
def downvote(memory_id) -> None

# Classify text.
def classify(text) -> Classification

# Export memories as formatted prompt.
def as_prompt(query, format="xml", max_tokens=None, limit=10,
              include_metadata=False) -> str

# Enrich memories with LLM metadata.
def enrich_memories(memory_ids=None, project=None, force=False) -> dict

# Extract facts from text.
def extract_facts(text) -> list[Fact]

# Get active facts.
def get_active_facts(subject=None, limit=50) -> list[Fact]

# List conflict log.
def list_conflicts(resolution=None, limit=10) -> list[ConflictEntry]

# Run consolidation (async).
async def consolidate(project=None, dry_run=True, strategy="all") -> ConsolidationResult

# Close the underlying store.
def close() -> None
```

#### Context Manager

```python
with Lore(project="my-project") as lore:
    lore.remember("Always use exponential backoff for rate limits")
    results = lore.recall("rate limiting")
# Store is automatically closed
```

### Data Types

```python
from lore.types import Memory, RecallResult, MemoryStats
from lore.types import Fact, ConflictEntry
from lore.types import Entity, Relationship, EntityMention, GraphContext
from lore.types import ConsolidationResult
from lore.classify.base import Classification
```

---

## HTTP API (Self-Hosted Server)

Base URL: `http://localhost:8765` (default) or your deployment URL.

All endpoints except `/health` require `Authorization: Bearer <api_key>`.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/v1/org/init` | Initialize organization |
| `POST` | `/v1/keys` | Create API key |
| `GET` | `/v1/keys` | List API keys |
| `DELETE` | `/v1/keys/{id}` | Revoke API key |
| `POST` | `/v1/lessons` | Create memory |
| `GET` | `/v1/lessons/{id}` | Get memory |
| `GET` | `/v1/lessons` | List memories |
| `PATCH` | `/v1/lessons/{id}` | Update memory |
| `DELETE` | `/v1/lessons/{id}` | Delete memory |
| `POST` | `/v1/lessons/search` | Semantic search |
| `POST` | `/v1/lessons/export` | Export all |
| `POST` | `/v1/lessons/import` | Bulk import |

Rate limit: 100 requests per 60 seconds per API key.

See [Self-Hosted Guide](self-hosted.md) for deployment instructions.
