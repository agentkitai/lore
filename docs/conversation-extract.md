# Conversation Auto-Extract API Reference

## Overview

Accept raw conversation messages and automatically extract salient memories (facts, decisions, preferences, lessons, corrections) using LLM processing. Requires `enrichment=True` with a configured LLM provider.

## SDK

### `lore.add_conversation()`

```python
from lore import Lore

lore = Lore(enrichment=True, enrichment_model="gpt-4o-mini")

result = lore.add_conversation(
    messages=[
        {"role": "user", "content": "How do I fix the CORS error?"},
        {"role": "assistant", "content": "Add the origin to allowed_origins in settings.py"},
    ],
    user_id="alice",           # optional — scope memories to user
    session_id="sess_abc123",  # optional — track session
    project="my-project",     # optional — project scope
)

print(result.status)             # "completed"
print(result.memories_extracted) # 1
print(result.memory_ids)         # ["01JABC..."]
print(result.duplicates_skipped) # 0
print(result.processing_time_ms) # 1200
```

**Parameters:**
- `messages` (required): List of `{"role": str, "content": str}` dicts
- `user_id` (optional): Scope extracted memories to this user
- `session_id` (optional): Session identifier for auditing
- `project` (optional): Project scope (defaults to Lore instance project)

**Returns:** `ConversationJob` dataclass

**Raises:**
- `RuntimeError` if enrichment/LLM not configured
- `ValueError` if messages is empty

### `lore.recall()` with `user_id`

```python
# Only Alice's memories
results = lore.recall("CORS fix", user_id="alice")

# All memories (backwards compatible)
results = lore.recall("CORS fix")
```

## CLI

```bash
# From file
lore add-conversation --file conversation.json --user-id alice --session-id sess_123

# From stdin
cat conversation.json | lore add-conversation --user-id alice

# Output:
# Accepted 15 messages for extraction.
# Extracted 4 memories, skipped 1 duplicate.
# Memory IDs: 01JABC..., 01JDEF..., 01JGHI..., 01JKLM...
# Estimated cost: ~$0.001 (150 tokens, gpt-4o-mini)
```

**Flags:**
- `--file`, `-f`: Path to JSON file with messages
- `--user-id`: Scope extracted memories to this user
- `--session-id`: Session identifier for tracking
- `--project`, `-p`: Project scope
- `--db`: Path to SQLite database

### JSON Input Formats

Wrapped format:
```json
{
    "messages": [
        {"role": "user", "content": "How do I deploy?"},
        {"role": "assistant", "content": "Use copilot deploy."}
    ]
}
```

Bare array format:
```json
[
    {"role": "user", "content": "How do I deploy?"},
    {"role": "assistant", "content": "Use copilot deploy."}
]
```

## MCP Tool

### `add_conversation`

```
Accept raw conversation messages and automatically extract memories.
Unlike 'remember' which requires you to decide what to save, this tool
accepts raw conversation history and uses LLM processing to extract
what's worth keeping.
```

**Parameters:**
- `messages` (required): List of `{"role": str, "content": str}` dicts
- `user_id` (optional): Scope memories to this user
- `session_id` (optional): Track conversation session
- `project` (optional): Project scope

**Returns:** Formatted string with extraction results

### `recall` (updated)

New optional `user_id` parameter filters memories by user scope.

## REST API

### POST `/v1/conversations`

Accept conversation for async extraction.

**Request:**
```json
{
    "messages": [
        {"role": "user", "content": "How do I deploy to ECS?"},
        {"role": "assistant", "content": "Use Fargate with 512MB memory."}
    ],
    "user_id": "alice",
    "session_id": "sess_abc123",
    "project": "my-project"
}
```

**Response (202 Accepted):**
```json
{
    "job_id": "01JEXAMPLE...",
    "status": "accepted",
    "message_count": 2
}
```

**Auth:** Requires `writer` or `admin` role.

### GET `/v1/conversations/{job_id}`

Check status of extraction job.

**Response (200 OK):**
```json
{
    "job_id": "01JEXAMPLE...",
    "status": "completed",
    "message_count": 2,
    "memories_extracted": 1,
    "memory_ids": ["01JABC..."],
    "duplicates_skipped": 0,
    "processing_time_ms": 1500,
    "error": null
}
```

**Status values:** `accepted` -> `processing` -> `completed` or `failed`

## User Scoping

- Memories extracted with `user_id="alice"` have `metadata.user_id = "alice"`
- `recall(query, user_id="alice")` returns only Alice's memories
- Memories without `user_id` are global (returned by all queries)
- `session_id` is stored for auditing but does not affect recall filtering

## LLM Requirement

Conversation extraction requires an LLM. Configure with:
- SDK: `Lore(enrichment=True, enrichment_model="gpt-4o-mini")`
- MCP: `LORE_ENRICHMENT_ENABLED=true`
- Without LLM: `add_conversation` returns a clear error message
