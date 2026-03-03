# Python SDK Guide

## Install

```bash
pip install lore-sdk
```

Extras:
```bash
pip install lore-sdk[mcp]     # MCP server support
pip install lore-sdk[server]  # REST API server
pip install lore-sdk[cli]     # CLI tool
pip install lore-sdk[remote]  # Remote store (httpx)
```

## Quick Start

```python
from lore import Lore

# Local mode — zero config, SQLite + ONNX embedding
client = Lore()

# Store a memory
mem_id = client.remember(
    content="Stripe rate-limits at 100 req/min. Use exponential backoff.",
    type="lesson",
    tags=["stripe", "rate-limit"],
)

# Search by meaning
results = client.recall("how to handle API rate limits", limit=5)
for r in results:
    print(f"[{r.score:.2f}] {r.memory.content}")

# List memories
memories, total = client.list_memories(type="lesson", limit=10)

# Stats
stats = client.memory_stats()
print(f"Total memories: {stats.total_count}")

# Delete
client.forget(id=mem_id)

client.close()
```

## Initialization

### Local mode (default)

```python
from lore import Lore

# Uses SQLite at ~/.lore/default.db
client = Lore()

# Custom database path
client = Lore(db_path="/path/to/my.db")

# With project scoping
client = Lore(project="my-project")
```

### Remote mode (connects to Lore server)

```python
from lore import Lore

client = Lore(
    store="remote",
    api_url="http://localhost:8765",
    api_key="lore_sk_...",
)
```

## Memory API

### `remember(content, type, tags, metadata, project, source, ttl)`

```python
# Basic
client.remember(content="PostgreSQL supports JSONB indexing")

# With all options
client.remember(
    content="Use connection pooling for PostgreSQL",
    type="lesson",
    tags=["postgres", "performance"],
    metadata={"severity": "high", "verified": True},
    project="backend",
    source="code-review",
    ttl="30d",  # Expires in 30 days
)
```

### `recall(query_text, type, tags, project, limit)`

```python
results = client.recall("database performance tips")

# With filters
results = client.recall(
    "rate limiting",
    type="lesson",
    tags=["api"],
    project="backend",
    limit=10,
)

for r in results:
    print(f"[{r.score:.2f}] {r.memory.content}")
    print(f"  Tags: {r.memory.tags}")
    print(f"  Created: {r.memory.created_at}")
```

### `forget(id, type, tags, project)`

```python
# Delete by ID
count = client.forget(id="01HXYZ...")

# Bulk delete by filter
count = client.forget(tags=["outdated"])
count = client.forget(type="note", project="old-project")
```

### `list_memories(type, tags, project, limit, offset, include_expired)`

```python
memories, total = client.list_memories(
    type="lesson",
    tags=["api"],
    limit=20,
    offset=0,
)
print(f"Showing {len(memories)} of {total}")
```

### `memory_stats(project)`

```python
stats = client.memory_stats()
print(f"Total: {stats.total_count}")
print(f"By type: {stats.count_by_type}")
print(f"By project: {stats.count_by_project}")
```

## Context Manager

```python
with Lore() as client:
    client.remember(content="Context managers auto-close")
# Automatically closed
```

## Types

```python
from lore import Memory, SearchResult, StoreStats

# Memory fields
memory.id          # str (ULID)
memory.content     # str
memory.type        # str (default: "note")
memory.tags        # List[str]
memory.metadata    # Dict[str, Any]
memory.project     # Optional[str]
memory.source      # Optional[str]
memory.created_at  # str (ISO 8601)
memory.updated_at  # str (ISO 8601)
memory.expires_at  # Optional[str]

# SearchResult fields
result.memory      # Memory
result.score       # float (0-1)

# StoreStats fields
stats.total_count      # int
stats.count_by_type    # Dict[str, int]
stats.count_by_project # Dict[str, int]
stats.oldest_memory    # Optional[str]
stats.newest_memory    # Optional[str]
```

## Legacy API

The original `publish()` / `query()` methods still work:

```python
# These still work but are deprecated
client.publish(problem="...", resolution="...")
results = client.query("search text")
```

Use `remember()` / `recall()` instead for new code.
