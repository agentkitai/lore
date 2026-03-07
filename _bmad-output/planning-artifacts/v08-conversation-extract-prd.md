# PRD: Conversation Auto-Extract

**Feature:** Conversation Auto-Extract Pipeline
**Version:** v0.8.0
**Status:** Draft
**Author:** John (PM)
**Date:** 2026-03-07
**Dependencies:** F3 (Consolidation) for deduplication, F6 (Metadata Enrichment) for LLM pipeline, F2 (Fact Extraction) for post-extraction enrichment
**Dependents:** None

---

## 1. Problem Statement

Lore requires agents to explicitly call `remember()` with pre-processed, curated content. This creates two critical failures:

1. **Unreliable capture** -- Agents don't consistently call `remember()`. Knowledge is lost because the agent must decide what's worth saving, interrupt its workflow to save it, and format the content appropriately. In practice, most agents skip this step entirely or do it inconsistently.

2. **Intelligence burden on the caller** -- The caller must decide *what* to extract and *how* to phrase it. This is backwards. The memory system should be intelligent enough to accept raw conversation and decide what's worth remembering.

Competitors (Mem0, Zep) solve this by accepting raw conversation data and automatically extracting memories using LLM processing. Lore's existing infrastructure (enrichment pipeline, consolidation engine, fact extraction) provides all the building blocks -- but there is no entry point that accepts raw conversations and orchestrates these components.

### Impact

Without this feature, Lore adoption requires agents/applications to be explicitly programmed to call `remember()` at the right moments with the right content. This is a significant integration barrier. With conversation auto-extract, any application can pipe its conversation history to Lore and get intelligent memory extraction with zero prompt engineering.

## 2. Goals

1. **Zero-intelligence ingestion** -- Accept raw conversation messages (role + content pairs) and automatically extract salient memories. The caller provides data; Lore provides intelligence.
2. **Leverage existing infrastructure** -- Use the enrichment pipeline (F6), fact extraction (F2), consolidation/dedup (F3), and classification (F9) that already exist. This is an orchestration feature, not a new AI capability.
3. **Multi-surface availability** -- Expose via REST API, MCP tool, CLI command, and Python SDK so any integration pattern works.
4. **Non-blocking ingestion** -- Accept the conversation and return immediately. Extraction happens asynchronously so callers aren't blocked by LLM processing time.
5. **Scoped memories** -- Support `user_id` and `session_id` to scope extracted memories per user/session, enabling per-user personalization use cases.

## 3. Non-Goals

- **Real-time streaming** -- No WebSocket/SSE streaming of conversations. Batch submission is sufficient for v1.
- **Multi-tenant auth** -- Use existing API key system. No per-user auth or tenant isolation beyond `user_id` metadata scoping.
- **Custom extraction prompts** -- Ship with sensible defaults. Configurable extraction prompts are a future enhancement.
- **Conversation threading** -- No tracking of conversation continuity across multiple submissions. Each submission is independent.
- **Token-level attribution** -- No tracking of which specific message produced which memory.
- **Outbound notifications** -- No webhooks/callbacks when extraction completes (v1 uses polling via job status endpoint).

## 4. User Stories

### US-1: Application Developer (REST API)
**As** an application developer building a chatbot,
**I want** to POST my conversation history to Lore after each session,
**so that** Lore automatically extracts and stores relevant knowledge without me deciding what to remember.

**Acceptance Criteria:**
- POST `/v1/conversations` with an array of `{role, content}` messages returns `202 Accepted` with a `job_id`
- GET `/v1/conversations/{job_id}` returns job status (`pending`, `processing`, `completed`, `failed`) and extracted memory IDs when complete
- Extracted memories appear in subsequent `recall()` queries
- Request works with `curl` and requires only `Content-Type: application/json` and `Authorization: Bearer <key>`

### US-2: AI Agent (MCP Tool)
**As** an AI agent using Lore via MCP,
**I want** an `add_conversation` tool that accepts my recent conversation,
**so that** I can dump my context at the end of a session and Lore handles the rest.

**Acceptance Criteria:**
- MCP tool `add_conversation` accepts `messages: list[{role: str, content: str}]`, optional `user_id`, optional `session_id`, optional `project`
- Tool returns immediately with `job_id` and `status: "accepted"`
- Extracted memories are tagged with `source: "conversation"` and include `session_id` in metadata
- Works with local SQLite store (no server required)

### US-3: Developer (CLI)
**As** a developer reviewing conversation logs,
**I want** to pipe a conversation file into `lore add-conversation`,
**so that** I can bulk-import knowledge from past sessions.

**Acceptance Criteria:**
- `lore add-conversation --file conversation.json` reads messages from a JSON file
- `cat conversation.json | lore add-conversation` reads from stdin
- Supports `--user-id`, `--session-id`, `--project` flags
- Outputs extracted memory count and IDs on completion
- JSON file format: `{"messages": [{"role": "user", "content": "..."}, ...]}` or bare array `[{"role": "...", "content": "..."}]`

### US-4: Python SDK User
**As** a Python developer integrating Lore,
**I want** `lore.add_conversation(messages, user_id=...)`,
**so that** I can programmatically feed conversations from my application.

**Acceptance Criteria:**
- `lore.add_conversation(messages=[{"role": "user", "content": "..."}], user_id="alice")` returns a `ConversationJob` with `job_id` and `status`
- `lore.conversation_status(job_id)` returns current status and extracted memory IDs
- For local store, extraction runs synchronously (background thread optional)
- For remote store, delegates to `POST /v1/conversations` on the server

### US-5: Per-User Memory Scoping
**As** an application serving multiple users,
**I want** to scope extracted memories by `user_id`,
**so that** each user's recalled memories are isolated.

**Acceptance Criteria:**
- Memories extracted with `user_id="alice"` are stored with `metadata.user_id = "alice"`
- `recall(query, user_id="alice")` only returns memories scoped to that user
- Memories without `user_id` are global (accessible to all)
- `session_id` is stored in metadata for auditing but does not affect recall filtering

## 5. Design

### 5.1 Conversation Message Format

```python
# Input message format
{
    "role": str,      # "user", "assistant", "system", "tool"
    "content": str    # message text content
}
```

### 5.2 API Endpoint

```
POST /v1/conversations
Content-Type: application/json
Authorization: Bearer <api_key>

{
    "messages": [
        {"role": "user", "content": "How do I deploy to ECS?"},
        {"role": "assistant", "content": "Use copilot deploy..."},
        {"role": "user", "content": "That worked, but I had to set the memory limit to 512MB"}
    ],
    "user_id": "alice",          // optional
    "session_id": "sess_abc123", // optional
    "project": "my-project"      // optional, defaults to server default
}
```

Response (202 Accepted):
```json
{
    "job_id": "01JEXAMPLE...",
    "status": "accepted",
    "message_count": 3
}
```

### 5.3 Job Status Endpoint

```
GET /v1/conversations/{job_id}
Authorization: Bearer <api_key>
```

Response (completed):
```json
{
    "job_id": "01JEXAMPLE...",
    "status": "completed",
    "message_count": 3,
    "memories_extracted": 2,
    "memory_ids": ["01JABC...", "01JDEF..."],
    "duplicates_skipped": 1,
    "processing_time_ms": 3200
}
```

### 5.4 Extraction Pipeline

The extraction pipeline orchestrates existing Lore components:

```
Raw Messages
    |
    v
[1. CONCATENATE] -- Combine messages into a structured conversation transcript
    |
    v
[2. EXTRACT]     -- LLM prompt identifies salient facts, decisions, preferences, lessons
    |                Returns candidate memories as structured content
    v
[3. DEDUPLICATE] -- Compare each candidate against existing store using cosine similarity
    |                Skip if similarity > 0.92 (configurable threshold)
    |                Use existing consolidation infrastructure
    v
[4. STORE]       -- Save deduplicated memories via remember()
    |                Tag with source="conversation", user_id, session_id
    v
[5. ENRICH]      -- Trigger existing enrichment pipeline (F6) on each new memory
    |                Classification (F9), fact extraction (F2), graph extraction
    v
[6. COMPLETE]    -- Update job status, record memory IDs
```

### 5.5 LLM Extraction Prompt

The extraction step uses the configured LLM (same `enrichment_model` setting as existing enrichment) with a prompt that identifies:

- **Facts** -- Concrete pieces of information ("ECS memory limit should be 512MB")
- **Decisions** -- Choices made during the conversation ("We'll use Fargate instead of EC2")
- **Preferences** -- User/team preferences ("Alice prefers dark mode", "Team uses pytest over unittest")
- **Lessons** -- Operational insights ("Deploy to staging before prod to catch memory issues")
- **Corrections** -- When earlier information was wrong and corrected later in the conversation

The prompt instructs the LLM to return structured JSON:
```json
{
    "memories": [
        {
            "content": "ECS task memory limit should be set to 512MB for the API service",
            "type": "fact",
            "confidence": 0.9,
            "tags": ["ecs", "deployment", "memory"]
        }
    ]
}
```

### 5.6 User/Session Scoping

Extracted memories include scoping metadata:

```python
memory.metadata = {
    "source": "conversation",
    "user_id": "alice",          # from request
    "session_id": "sess_abc123", # from request
    "extracted_at": "2026-03-07T10:00:00Z",
    "extraction_model": "gpt-4o-mini",
    "conversation_length": 3     # number of messages
}
```

The `user_id` field is indexed and used as a filter in `recall()`. This extends the existing `recall()` interface with an optional `user_id` parameter.

### 5.7 Async Processing (Server Mode)

For the REST API server:
- Request handler validates the payload, creates a job record, and returns `202 Accepted`
- A background worker (asyncio task or thread pool) picks up the job and runs the extraction pipeline
- Job status is stored in the database (new `conversation_jobs` table)
- Workers process jobs FIFO with configurable concurrency (default: 2 concurrent extractions)

For local SDK mode:
- `add_conversation()` runs extraction synchronously by default
- Optional `background=True` parameter spawns a thread (for CLI/SDK use)

### 5.8 MCP Tool Definition

```python
@mcp.tool()
def add_conversation(
    messages: list[dict],      # [{role: str, content: str}, ...]
    user_id: str = None,       # scope memories to this user
    session_id: str = None,    # track conversation session
    project: str = None,       # project scope (defaults to configured project)
) -> dict:
    """Accept raw conversation messages and automatically extract memories.

    Unlike 'remember' which requires pre-processed content, this tool accepts
    raw conversation history and uses LLM processing to identify and store
    salient facts, decisions, preferences, and lessons.
    """
```

### 5.9 CLI Command

```
# From file
lore add-conversation --file conversation.json --user-id alice --session-id sess_123

# From stdin
cat conversation.json | lore add-conversation --user-id alice

# Output
Accepted 15 messages for extraction.
Extracted 4 memories, skipped 1 duplicate.
Memory IDs: 01JABC..., 01JDEF..., 01JGHI..., 01JKLM...
```

### 5.10 SDK Method

```python
from lore import Lore

lore = Lore(enrichment=True, enrichment_model="gpt-4o-mini")

# Synchronous (local store)
result = lore.add_conversation(
    messages=[
        {"role": "user", "content": "How do I fix the CORS error?"},
        {"role": "assistant", "content": "Add the origin to allowed_origins in settings.py"},
    ],
    user_id="alice",
    session_id="sess_abc",
)
print(result.memories_extracted)  # 1
print(result.memory_ids)          # ["01JABC..."]

# Remote store (async, returns job)
job = lore.add_conversation(messages=messages, user_id="alice")
print(job.status)  # "accepted"
status = lore.conversation_status(job.job_id)
```

## 6. Data Model Changes

### 6.1 New Table: `conversation_jobs` (server mode only)

| Column | Type | Description |
|--------|------|-------------|
| `id` | `TEXT PK` | ULID job ID |
| `status` | `TEXT` | `accepted`, `processing`, `completed`, `failed` |
| `message_count` | `INTEGER` | Number of input messages |
| `messages_json` | `TEXT` | Stored conversation payload (JSON) |
| `user_id` | `TEXT` | Optional user scope |
| `session_id` | `TEXT` | Optional session ID |
| `project` | `TEXT` | Project scope |
| `memory_ids` | `TEXT` | JSON array of extracted memory IDs |
| `memories_extracted` | `INTEGER` | Count of new memories created |
| `duplicates_skipped` | `INTEGER` | Count of skipped duplicates |
| `error` | `TEXT` | Error message if failed |
| `processing_time_ms` | `INTEGER` | Total extraction time |
| `created_at` | `TEXT` | ISO 8601 timestamp |
| `completed_at` | `TEXT` | ISO 8601 timestamp |

### 6.2 Recall Extension

The `recall()` method and MCP tool gain an optional `user_id` parameter:
- When set, only memories with matching `metadata.user_id` are returned
- When unset, all memories are returned (backwards compatible)

## 7. Success Metrics

| Metric | Target | How to Measure |
|--------|--------|----------------|
| **Extraction precision** | >80% of extracted memories are genuinely useful (not noise) | Manual review of 50 extraction runs across diverse conversations |
| **Deduplication accuracy** | <5% false positives (useful memories skipped), <10% false negatives (duplicates stored) | Compare extracted memories against existing store on test corpus |
| **Latency (local)** | <10s for a 20-message conversation with gpt-4o-mini | Benchmark test |
| **Latency (server)** | <500ms acceptance time; <30s total processing for 20 messages | Benchmark test |
| **Adoption** | 3+ integrations using `add_conversation` within 30 days of release | Track usage via `source: "conversation"` metadata |
| **Memory quality** | Extracted memories achieve >0.7 average recall relevance score | Compare recall scores of auto-extracted vs manually-created memories |

## 8. Technical Constraints

1. **LLM required** -- Extraction requires a configured LLM (`enrichment=True`). Without an LLM, `add_conversation` raises a clear error explaining the requirement. This is different from `remember()` which works without an LLM.

2. **Token limits** -- Conversations may exceed the LLM's context window. The pipeline must chunk long conversations (>8K tokens) and process chunks independently with overlap for context continuity.

3. **Cost awareness** -- Each extraction makes at least one LLM call. For gpt-4o-mini at ~$0.15/1M input tokens, a 20-message conversation costs ~$0.001. Document this and make it visible in the CLI output.

4. **Existing embedder** -- Use the existing `LocalEmbedder` (384-dim ONNX) for deduplication similarity. No new embedding infrastructure needed.

5. **Existing store interface** -- Extracted memories are stored via `remember()`, not by directly writing to the store. This ensures all existing pipelines (enrichment, classification, fact extraction, graph) are triggered.

6. **No new dependencies** -- Use existing `lore.llm` module for LLM calls. No new Python packages required.

7. **Backwards compatibility** -- All existing APIs, tools, and CLI commands continue to work unchanged. `add_conversation` is purely additive.

## 9. Phased Delivery Plan

### Phase 1: Core Extraction Pipeline (P0)
**Scope:** LLM extraction prompt, deduplication, SDK method, basic CLI
**Deliverables:**
- Extraction prompt template in `src/lore/conversation/prompts.py`
- `ConversationExtractor` class orchestrating extract -> dedup -> store
- `Lore.add_conversation()` SDK method (synchronous, local store)
- `lore add-conversation` CLI command (file + stdin)
- Unit tests for extraction parsing, dedup logic
- Integration test: conversation in -> memories out -> recall finds them

**Exit Criteria:**
- `lore.add_conversation(messages)` extracts and stores memories from a 20-message conversation
- Duplicates are detected and skipped
- Extracted memories are retrievable via `recall()`

### Phase 2: MCP + User Scoping (P0)
**Scope:** MCP tool, user_id/session_id support, recall filtering
**Deliverables:**
- `add_conversation` MCP tool
- `user_id` and `session_id` metadata on extracted memories
- `user_id` filter on `recall()` MCP tool and SDK method
- Tests for scoped recall

**Exit Criteria:**
- MCP tool accepts messages and returns job info
- `recall(query, user_id="alice")` only returns Alice's memories
- Existing recall behavior unchanged when `user_id` is not provided

### Phase 3: REST API + Async Processing (P1)
**Scope:** Server endpoint, background processing, job status
**Deliverables:**
- `POST /v1/conversations` endpoint in server routes
- `GET /v1/conversations/{job_id}` status endpoint
- `conversation_jobs` database table and migration
- Background worker with configurable concurrency
- Server integration tests

**Exit Criteria:**
- `curl -X POST /v1/conversations` returns 202 with job_id
- Job progresses through accepted -> processing -> completed
- Extracted memories appear in recall after job completes
- Multiple jobs can process concurrently

### Phase 4: Hardening + Documentation (P1)
**Scope:** Error handling, chunking, cost tracking, docs
**Deliverables:**
- Conversation chunking for long inputs (>8K tokens)
- Cost estimation in CLI output
- Error recovery (partial extraction on LLM failure)
- API reference documentation
- Usage guide with examples for each surface (API, MCP, CLI, SDK)
- CHANGELOG entry

**Exit Criteria:**
- 50-message conversation processes without error
- Failed LLM call doesn't lose already-extracted memories
- Documentation covers all integration patterns

## 10. Open Questions

1. **Conversation retention** -- Should the raw conversation be stored permanently (for re-extraction with improved prompts later) or discarded after extraction? Storing adds storage cost but enables replay.

2. **Incremental extraction** -- If the same `session_id` submits messages multiple times (growing conversation), should we re-extract the full conversation or only process new messages? V1 treats each submission independently.

3. **System messages** -- Should system prompts be included in extraction, or filtered out? They may contain useful context (tool definitions, persona instructions) but also noise.

4. **Extraction quality feedback** -- Should there be a way to upvote/downvote extracted memories to improve future extraction? The existing `upvote_memory`/`downvote_memory` tools work for individual memories but don't feed back into the extraction prompt.
