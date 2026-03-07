# v0.8.0: Conversation Auto-Extract — Architecture Document

## Overview

Accept raw conversation messages and automatically extract salient memories using the existing LLM, enrichment, consolidation, and fact extraction infrastructure. This is an orchestration feature — no new AI capabilities, just a new entry point that wires existing components into a conversation-to-memory pipeline.

## 1. Module Structure

```
src/lore/
├── conversation/                 # NEW package
│   ├── __init__.py               # Public API: ConversationExtractor, ConversationJob
│   ├── extractor.py              # ConversationExtractor class (pipeline orchestrator)
│   ├── prompts.py                # Extraction prompt template
│   └── chunker.py                # Token-aware conversation chunking
├── lore.py                       # Modified: add_conversation(), conversation_status()
├── cli.py                        # Modified: add-conversation subcommand
├── mcp/server.py                 # Modified: add_conversation tool
├── types.py                      # Modified: ConversationJob dataclass
├── server/
│   ├── app.py                    # Modified: include conversations router
│   ├── models.py                 # Modified: request/response models
│   └── routes/
│       └── conversations.py      # NEW: POST/GET /v1/conversations
```

### Placement rationale
- `src/lore/conversation/` follows the existing pattern of feature packages (`extract/`, `enrichment/`, `classify/`, `ingest/`).
- The extractor lives in its own package because it has its own prompts, chunking logic, and will grow with custom extraction strategies later.
- Server routes follow the existing `routes/lessons.py` pattern.

## 2. Data Types

### 2.1 ConversationMessage (types.py)

```python
@dataclass
class ConversationMessage:
    """A single message in a conversation."""
    role: str      # "user", "assistant", "system", "tool"
    content: str

@dataclass
class ConversationJob:
    """Result of a conversation extraction job."""
    job_id: str
    status: str                        # "accepted", "processing", "completed", "failed"
    message_count: int = 0
    memories_extracted: int = 0
    memory_ids: List[str] = field(default_factory=list)
    duplicates_skipped: int = 0
    processing_time_ms: int = 0
    error: Optional[str] = None
```

These follow the existing pattern of dataclasses in `types.py` (like `Memory`, `Fact`, `ConsolidationResult`).

## 3. ConversationExtractor (conversation/extractor.py)

### 3.1 Class Design

```python
class ConversationExtractor:
    """Orchestrates: concat → extract → dedup → store → enrich."""

    def __init__(
        self,
        lore: "Lore",
        dedup_threshold: float = 0.92,
    ) -> None:
        self._lore = lore
        self._embedder = lore._embedder
        self._store = lore._store
        self._enrichment_pipeline = lore._enrichment_pipeline
        self._dedup_threshold = dedup_threshold
```

### Why pass `Lore` instance (not individual components)

The extractor calls `lore.remember()` for each extracted memory. This is a deliberate design choice from the PRD (§8.5): "Extracted memories are stored via `remember()`, not by directly writing to the store." This ensures all existing pipelines (enrichment, classification, fact extraction, graph) are triggered automatically. Passing the full Lore instance avoids duplicating the remember() wiring.

### 3.2 extract() Method — Main Pipeline

```python
def extract(
    self,
    messages: List[ConversationMessage],
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    project: Optional[str] = None,
) -> ConversationJob:
    """Run the full extraction pipeline synchronously."""
```

Pipeline stages:

1. **VALIDATE** — Check messages non-empty, LLM available
2. **CONCATENATE** — Format messages into a transcript string
3. **CHUNK** — Split long transcripts (>8K tokens) with overlap
4. **EXTRACT** — LLM call per chunk → list of candidate memories
5. **DEDUPLICATE** — Cosine similarity against existing store
6. **STORE** — `lore.remember()` for each unique candidate
7. **RETURN** — Build `ConversationJob` with results

### 3.3 Stage Details

#### Stage 1: VALIDATE

```python
if not messages:
    raise ValueError("messages must be non-empty")
if self._lore._enrichment_pipeline is None:
    raise RuntimeError(
        "Conversation extraction requires an LLM. "
        "Initialize Lore with enrichment=True and configure an LLM provider."
    )
```

Uses the `enrichment_pipeline`'s LLM client, same as the PRD specifies: "Use the configured LLM (same `enrichment_model` setting as existing enrichment)."

#### Stage 2: CONCATENATE

```python
def _format_transcript(self, messages: List[ConversationMessage]) -> str:
    """Format messages into a structured transcript."""
    lines = []
    for msg in messages:
        lines.append(f"[{msg.role}]: {msg.content}")
    return "\n\n".join(lines)
```

Simple, role-prefixed format. No special handling for system messages in v1 (PRD open question #3 — include them as-is).

#### Stage 3: CHUNK (conversation/chunker.py)

```python
class ConversationChunker:
    """Token-aware chunking for long conversations."""

    def __init__(self, max_tokens: int = 8000, overlap_messages: int = 2):
        self.max_tokens = max_tokens
        self.overlap_messages = overlap_messages

    def chunk(self, messages: List[ConversationMessage]) -> List[List[ConversationMessage]]:
        """Split messages into chunks that fit within token limits.

        Uses simple word-count heuristic (1 token ≈ 0.75 words).
        Overlap: last N messages of chunk i are prepended to chunk i+1
        for context continuity.
        """
```

Token estimation uses `len(text.split()) / 0.75` — good enough for chunking decisions without adding a tokenizer dependency (no new deps per PRD §8.6). The overlap ensures context continuity across chunk boundaries.

#### Stage 4: EXTRACT (conversation/prompts.py)

The extraction prompt:

```python
CONVERSATION_EXTRACT_PROMPT = """You are a memory extraction system. Analyze the following conversation and extract salient pieces of knowledge worth remembering long-term.

Extract these types of memories:
- **Facts**: Concrete pieces of information (e.g., "ECS memory limit should be 512MB")
- **Decisions**: Choices made during the conversation (e.g., "Using Fargate instead of EC2")
- **Preferences**: User or team preferences (e.g., "Prefers pytest over unittest")
- **Lessons**: Operational insights learned (e.g., "Deploy to staging first to catch memory issues")
- **Corrections**: When earlier information was corrected later in the conversation

Rules:
- Only extract genuinely useful, non-obvious knowledge
- Each memory should be self-contained and understandable without the conversation
- Do NOT extract greetings, acknowledgments, or trivial exchanges
- If the conversation has no extractable knowledge, return an empty list
- Assign a confidence score (0.0-1.0) based on how clearly stated the information is
- Suggest relevant tags (1-5 lowercase tags per memory)

Respond with JSON only:
{
    "memories": [
        {
            "content": "Clear, self-contained statement of the knowledge",
            "type": "fact|decision|preference|lesson|correction",
            "confidence": 0.9,
            "tags": ["relevant", "tags"]
        }
    ]
}

Conversation:
---
{transcript}
---"""
```

LLM call uses `self._lore._enrichment_pipeline.llm.complete()`:

```python
def _extract_candidates(self, transcript: str) -> List[Dict[str, Any]]:
    """Call LLM to extract memory candidates from transcript."""
    prompt = CONVERSATION_EXTRACT_PROMPT.format(transcript=transcript)
    response = self._lore._enrichment_pipeline.llm.complete(prompt)
    return self._parse_extraction_response(response)
```

The LLM client is reused from the enrichment pipeline (`LLMClient` from `lore/enrichment/llm.py`), which supports OpenAI, Anthropic, and Google via litellm. No new LLM infrastructure needed.

#### Stage 5: DEDUPLICATE

```python
def _is_duplicate(self, content: str) -> bool:
    """Check if candidate memory is too similar to existing memories."""
    embedding = self._embedder.embed(content)
    # Use the same recall mechanism to find similar memories
    results = self._lore.recall(content, limit=3)
    for r in results:
        if r.score >= self._dedup_threshold:
            return True
    return False
```

Uses `lore.recall()` with the candidate content as the query. If any existing memory has cosine similarity >= 0.92 (configurable), the candidate is skipped. This reuses the existing embedding + similarity infrastructure — no new dedup code needed.

**Why 0.92 threshold?** Lower than the consolidation dedup threshold (0.95) because conversation-extracted memories tend to be shorter and more specific. A memory about "ECS memory limit is 512MB" should match "ECS tasks need 512MB memory" but not "ECS deployment uses Fargate."

#### Stage 6: STORE

```python
for candidate in unique_candidates:
    memory_type = self._map_type(candidate["type"])
    metadata = {
        "source": "conversation",
        "user_id": user_id,
        "session_id": session_id,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "extraction_model": self._lore._enrichment_pipeline.llm.model,
        "conversation_length": len(messages),
    }
    memory_id = self._lore.remember(
        content=candidate["content"],
        type=memory_type,
        tier="long",
        tags=candidate.get("tags", []),
        metadata=metadata,
        source="conversation",
        project=project or self._lore.project,
        confidence=candidate.get("confidence", 0.8),
    )
    job.memory_ids.append(memory_id)
```

**Type mapping**: The LLM returns types like "fact", "decision", "preference", "lesson", "correction". These map to existing `VALID_MEMORY_TYPES`:
- `fact` → `"fact"`
- `decision` → `"general"` (no "decision" type exists)
- `preference` → `"preference"`
- `lesson` → `"lesson"`
- `correction` → `"general"` (corrections update the knowledge base; the corrected info is the fact)

Calling `lore.remember()` triggers the full pipeline: embedding, redaction, classification, enrichment, fact extraction, graph update. This is the key architectural choice — we don't bypass any existing functionality.

## 4. Data Flow

```
User calls add_conversation(messages, user_id, session_id)
    │
    ▼
ConversationExtractor.extract()
    │
    ├── 1. Validate: check messages + LLM configured
    │
    ├── 2. Format transcript: messages → "[role]: content\n\n..."
    │
    ├── 3. Chunk if needed: split at ~8K tokens, 2-message overlap
    │
    ├── 4. Per chunk:
    │   ├── LLM call → JSON with candidate memories
    │   └── Parse + validate candidates
    │
    ├── 5. Per candidate:
    │   ├── Embed candidate content
    │   ├── recall(content, limit=3) → check similarity
    │   ├── Skip if score >= 0.92 (duplicate)
    │   └── lore.remember(content, metadata={source, user_id, session_id})
    │       ├── Redaction scan
    │       ├── Embedding
    │       ├── Classification (if enabled)
    │       ├── Enrichment (if enabled)
    │       ├── Fact extraction (if enabled)
    │       └── Graph update (if enabled)
    │
    └── 6. Return ConversationJob(status="completed", memory_ids=[...])
```

## 5. Database Changes

### 5.1 conversation_jobs Table (Server Mode Only)

New migration file: `migrations/005_conversation_jobs.sql`

```sql
CREATE TABLE IF NOT EXISTS conversation_jobs (
    id            TEXT PRIMARY KEY,
    org_id        TEXT NOT NULL REFERENCES orgs(id),
    status        TEXT NOT NULL DEFAULT 'accepted',
    message_count INTEGER NOT NULL DEFAULT 0,
    messages_json TEXT,                -- stored conversation (JSON)
    user_id       TEXT,                -- optional user scope
    session_id    TEXT,                -- optional session ID
    project       TEXT,                -- project scope
    memory_ids    TEXT DEFAULT '[]',   -- JSON array of extracted memory IDs
    memories_extracted INTEGER DEFAULT 0,
    duplicates_skipped INTEGER DEFAULT 0,
    error         TEXT,
    processing_time_ms INTEGER DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at  TIMESTAMPTZ
);

CREATE INDEX idx_conversation_jobs_org_id ON conversation_jobs(org_id);
CREATE INDEX idx_conversation_jobs_status ON conversation_jobs(status);
```

This table is only used in server mode (PostgreSQL). The local SQLite mode runs extraction synchronously and returns results directly — no job tracking needed.

### 5.2 No Changes to Existing Tables

The PRD mentions "user_id indexing on lessons table" but this is unnecessary for the local SQLite store: `user_id` is stored in memory `metadata` (JSON) and filtered in Python during recall. For the server mode, `user_id` filtering on `recall()` would be a separate small PR — it's a query-side change to the existing search endpoint, not an extraction concern.

**Decision**: `user_id` is stored in `metadata.user_id` on each extracted memory. Recall filtering by `user_id` is deferred to Phase 2 (MCP + User Scoping) as it touches the recall pipeline, not the extraction pipeline.

## 6. API Route Design (server/routes/conversations.py)

### 6.1 POST /v1/conversations

```python
router = APIRouter(prefix="/v1/conversations", tags=["conversations"])

class ConversationRequest(BaseModel):
    messages: List[Dict[str, str]]  # [{role, content}, ...]
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    project: Optional[str] = None

class ConversationAcceptedResponse(BaseModel):
    job_id: str
    status: str = "accepted"
    message_count: int

@router.post("", response_model=ConversationAcceptedResponse, status_code=202)
async def create_conversation_job(
    body: ConversationRequest,
    auth: AuthContext = Depends(require_role("writer", "admin")),
) -> ConversationAcceptedResponse:
    """Accept conversation for async extraction."""
    # Validate messages
    if not body.messages:
        raise HTTPException(400, "messages must be non-empty")
    for msg in body.messages:
        if "role" not in msg or "content" not in msg:
            raise HTTPException(400, "Each message must have 'role' and 'content'")

    job_id = str(ULID())
    now = datetime.now(timezone.utc)

    # Insert job record
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO conversation_jobs
               (id, org_id, status, message_count, messages_json,
                user_id, session_id, project, created_at)
               VALUES ($1, $2, 'accepted', $3, $4, $5, $6, $7, $8)""",
            job_id, auth.org_id, len(body.messages),
            json.dumps(body.messages),
            body.user_id, body.session_id,
            body.project or auth.project, now,
        )

    # Dispatch to background worker
    asyncio.create_task(_process_job(job_id))

    return ConversationAcceptedResponse(
        job_id=job_id,
        status="accepted",
        message_count=len(body.messages),
    )
```

### 6.2 GET /v1/conversations/{job_id}

```python
class ConversationStatusResponse(BaseModel):
    job_id: str
    status: str
    message_count: int
    memories_extracted: int = 0
    memory_ids: List[str] = []
    duplicates_skipped: int = 0
    processing_time_ms: int = 0
    error: Optional[str] = None

@router.get("/{job_id}", response_model=ConversationStatusResponse)
async def get_conversation_status(
    job_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> ConversationStatusResponse:
    """Check status of a conversation extraction job."""
    scope_sql, scope_params = _scope_filter(auth)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""SELECT id, status, message_count, memory_ids,
                       memories_extracted, duplicates_skipped,
                       processing_time_ms, error
                FROM conversation_jobs
                WHERE id = ${len(scope_params) + 1} AND {scope_sql}""",
            *scope_params, job_id,
        )
    if row is None:
        raise HTTPException(404, "Job not found")
    return ConversationStatusResponse(
        job_id=row["id"],
        status=row["status"],
        message_count=row["message_count"],
        memories_extracted=row["memories_extracted"] or 0,
        memory_ids=json.loads(row["memory_ids"] or "[]"),
        duplicates_skipped=row["duplicates_skipped"] or 0,
        processing_time_ms=row["processing_time_ms"] or 0,
        error=row["error"],
    )
```

### 6.3 Background Worker

```python
async def _process_job(job_id: str) -> None:
    """Background task: run extraction pipeline and update job record."""
    pool = await get_pool()

    # Update status to processing
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE conversation_jobs SET status = 'processing' "
            "WHERE id = $1 RETURNING messages_json, user_id, session_id, project",
            job_id,
        )

    start = time.monotonic()
    try:
        messages = json.loads(row["messages_json"])
        conv_messages = [
            ConversationMessage(role=m["role"], content=m["content"])
            for m in messages
        ]

        # Create a Lore instance with enrichment for extraction
        lore = _get_server_lore()
        extractor = ConversationExtractor(lore)
        result = extractor.extract(
            conv_messages,
            user_id=row["user_id"],
            session_id=row["session_id"],
            project=row["project"],
        )

        elapsed_ms = int((time.monotonic() - start) * 1000)

        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE conversation_jobs SET
                       status = 'completed',
                       memories_extracted = $2,
                       memory_ids = $3,
                       duplicates_skipped = $4,
                       processing_time_ms = $5,
                       completed_at = now()
                   WHERE id = $1""",
                job_id, result.memories_extracted,
                json.dumps(result.memory_ids),
                result.duplicates_skipped, elapsed_ms,
            )
    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE conversation_jobs SET
                       status = 'failed', error = $2,
                       processing_time_ms = $3,
                       completed_at = now()
                   WHERE id = $1""",
                job_id, str(e), elapsed_ms,
            )
```

The background worker uses `asyncio.create_task()` — matching the existing pattern in `ConsolidationScheduler` which uses `asyncio.ensure_future()`. No thread pool needed; the LLM calls in litellm are I/O-bound and asyncio-compatible.

## 7. MCP Tool Integration (mcp/server.py)

```python
@mcp.tool(
    description=(
        "Accept raw conversation messages and automatically extract memories. "
        "USE THIS WHEN: you want to dump your recent conversation context so Lore "
        "can identify and store useful knowledge (facts, decisions, preferences, lessons). "
        "Unlike 'remember' which requires you to decide what to save, this tool accepts "
        "raw conversation history and uses LLM processing to extract what's worth keeping. "
        "Requires enrichment to be enabled (LORE_ENRICHMENT_ENABLED=true)."
    ),
)
def add_conversation(
    messages: List[Dict[str, str]],
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    project: Optional[str] = None,
) -> str:
    """Accept raw conversation and extract memories."""
    try:
        lore = _get_lore()
        result = lore.add_conversation(
            messages=messages,
            user_id=user_id,
            session_id=session_id,
            project=project,
        )
        lines = [
            f"Extracted {result.memories_extracted} memories from {result.message_count} messages.",
        ]
        if result.duplicates_skipped:
            lines.append(f"Skipped {result.duplicates_skipped} duplicates.")
        if result.memory_ids:
            lines.append(f"Memory IDs: {', '.join(result.memory_ids)}")
        return "\n".join(lines)
    except RuntimeError as e:
        return str(e)
    except Exception as e:
        return f"Conversation extraction failed: {e}"
```

Follows the existing MCP tool patterns: wraps `_get_lore()`, calls the SDK method, formats output as a string, catches exceptions gracefully.

## 8. CLI Command Design (cli.py)

```python
def cmd_add_conversation(args: argparse.Namespace) -> None:
    """Handle add-conversation subcommand."""
    # Read messages from file or stdin
    if args.file:
        with open(args.file, "r") as f:
            data = json.load(f)
    elif not sys.stdin.isatty():
        data = json.load(sys.stdin)
    else:
        print("Error: provide --file or pipe JSON to stdin", file=sys.stderr)
        sys.exit(1)

    # Accept both {"messages": [...]} and bare [...]
    if isinstance(data, list):
        messages = data
    elif isinstance(data, dict) and "messages" in data:
        messages = data["messages"]
    else:
        print("Error: JSON must be a list or {\"messages\": [...]}", file=sys.stderr)
        sys.exit(1)

    lore = _get_lore(args.db)
    result = lore.add_conversation(
        messages=messages,
        user_id=getattr(args, "user_id", None),
        session_id=getattr(args, "session_id", None),
        project=args.project,
    )
    lore.close()

    print(f"Accepted {result.message_count} messages for extraction.")
    print(f"Extracted {result.memories_extracted} memories, skipped {result.duplicates_skipped} duplicates.")
    if result.memory_ids:
        print(f"Memory IDs: {', '.join(result.memory_ids)}")
```

Subparser registration (in `build_parser()`):

```python
p_conv = sub.add_parser("add-conversation", help="Extract memories from conversation")
p_conv.add_argument("--file", "-f", help="Path to JSON file with messages")
p_conv.add_argument("--user-id", help="Scope extracted memories to this user")
p_conv.add_argument("--session-id", help="Session identifier for tracking")
p_conv.add_argument("--project", "-p", help="Project scope")
p_conv.add_argument("--db", help="Path to SQLite database")
p_conv.set_defaults(func=cmd_add_conversation)
```

## 9. SDK Method Design (lore.py)

```python
def add_conversation(
    self,
    messages: List[Dict[str, str]],
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    project: Optional[str] = None,
) -> ConversationJob:
    """Extract memories from raw conversation messages.

    Requires enrichment=True (LLM needed for extraction).
    Runs synchronously for local store. For remote store,
    delegates to POST /v1/conversations.

    Args:
        messages: List of {role, content} dicts.
        user_id: Scope extracted memories to this user.
        session_id: Track which conversation session this came from.
        project: Project scope (defaults to self.project).

    Returns:
        ConversationJob with extraction results.

    Raises:
        RuntimeError: If enrichment/LLM not configured.
        ValueError: If messages is empty or malformed.
    """
    from lore.conversation import ConversationExtractor
    from lore.types import ConversationMessage

    # Remote store: delegate to server
    if hasattr(self._store, 'search') and hasattr(self._store, '_api_url'):
        return self._add_conversation_remote(messages, user_id, session_id, project)

    # Local: run synchronously
    conv_messages = [
        ConversationMessage(role=m["role"], content=m["content"])
        for m in messages
    ]
    extractor = ConversationExtractor(self)
    return extractor.extract(
        conv_messages,
        user_id=user_id,
        session_id=session_id,
        project=project,
    )

def conversation_status(self, job_id: str) -> ConversationJob:
    """Check status of a conversation extraction job (remote store only)."""
    if not hasattr(self._store, '_api_url'):
        raise RuntimeError("conversation_status() is only for remote store")
    return self._conversation_status_remote(job_id)
```

For remote store, `_add_conversation_remote()` calls `POST /v1/conversations` via the `HttpStore`'s HTTP client. For local store, extraction runs synchronously inline.

## 10. Error Handling

### 10.1 LLM Failures

```python
try:
    candidates = self._extract_candidates(transcript)
except Exception as e:
    logger.warning("LLM extraction failed for chunk: %s", e)
    # For multi-chunk: continue with remaining chunks
    # For single chunk: raise to caller
    if len(chunks) > 1:
        continue
    raise RuntimeError(f"Extraction failed: {e}")
```

**Partial extraction**: If processing multiple chunks and one fails, the successfully-extracted memories from other chunks are still stored. The `ConversationJob` reports `memories_extracted` for whatever succeeded, and `error` contains the failure details.

### 10.2 Malformed LLM Response

Follow the same pattern as `FactExtractor._parse_response()` and `EnrichmentPipeline._parse_and_validate()`:

```python
def _parse_extraction_response(self, response: str) -> List[Dict[str, Any]]:
    """Parse LLM JSON response. Best-effort: returns partial results."""
    text = response.strip()
    # Strip markdown code blocks
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Conversation extract: malformed JSON: %s", text[:200])
        return []

    memories = data.get("memories", [])
    if not isinstance(memories, list):
        return []

    # Validate each candidate
    valid = []
    for m in memories:
        if not isinstance(m, dict):
            continue
        content = str(m.get("content", "")).strip()
        if not content:
            continue
        valid.append({
            "content": content,
            "type": m.get("type", "general"),
            "confidence": max(0.0, min(1.0, float(m.get("confidence", 0.8)))),
            "tags": [str(t).lower() for t in m.get("tags", []) if isinstance(t, str)][:5],
        })
    return valid
```

### 10.3 Token Limits

The `ConversationChunker` handles conversations that exceed the LLM context window:
- Default chunk size: 8000 tokens (~6000 words)
- Overlap: last 2 messages from previous chunk prepended for context continuity
- Token estimation: `len(text.split()) / 0.75` (no tokenizer dependency)

### 10.4 Empty Extraction

If the LLM returns an empty `memories` array (conversation had no extractable knowledge), this is a valid result — `ConversationJob` returns with `memories_extracted=0, status="completed"`.

## 11. Testing Strategy

### 11.1 Unit Tests (tests/test_conversation_extractor.py)

| Test | What it validates |
|------|-------------------|
| `test_format_transcript` | Messages correctly formatted as `[role]: content` |
| `test_parse_extraction_valid` | Valid LLM JSON parsed to candidate list |
| `test_parse_extraction_malformed` | Malformed JSON returns empty list (no crash) |
| `test_parse_extraction_markdown_wrapped` | JSON in ``` blocks extracted correctly |
| `test_type_mapping` | LLM types mapped to VALID_MEMORY_TYPES |
| `test_confidence_clamping` | Out-of-range confidence clamped to [0, 1] |
| `test_empty_messages` | ValueError raised for empty messages list |
| `test_no_llm_configured` | RuntimeError with clear message |

### 11.2 Unit Tests (tests/test_conversation_chunker.py)

| Test | What it validates |
|------|-------------------|
| `test_short_conversation_no_chunk` | <8K tokens returns single chunk |
| `test_long_conversation_chunks` | >8K tokens split into multiple chunks |
| `test_overlap_messages` | Last 2 messages appear at start of next chunk |
| `test_single_huge_message` | One message >8K tokens still works (single chunk) |

### 11.3 Unit Tests (tests/test_conversation_dedup.py)

| Test | What it validates |
|------|-------------------|
| `test_unique_candidate_stored` | New content passes dedup |
| `test_duplicate_candidate_skipped` | Similar content (>0.92) skipped |
| `test_threshold_boundary` | Content at exactly threshold is skipped |

### 11.4 Integration Tests (tests/test_conversation_integration.py)

| Test | What it validates |
|------|-------------------|
| `test_end_to_end_extract` | messages in → memories out via `lore.add_conversation()` |
| `test_extracted_memories_recallable` | `recall()` finds auto-extracted memories |
| `test_metadata_persisted` | `source=conversation`, `user_id`, `session_id` in metadata |
| `test_enrichment_triggered` | Extracted memories have enrichment metadata |
| `test_dedup_across_conversations` | Second extraction of same conversation yields 0 new |

### 11.5 CLI Tests (tests/test_cli_conversation.py)

| Test | What it validates |
|------|-------------------|
| `test_add_conversation_file` | `--file` reads and processes JSON file |
| `test_add_conversation_stdin` | Piped JSON processed correctly |
| `test_add_conversation_bare_array` | Bare `[{...}]` format accepted |
| `test_add_conversation_wrapped` | `{"messages": [...]}` format accepted |

### 11.6 Mocking Strategy

- **LLM calls**: Mock `enrichment_pipeline.llm.complete()` to return canned extraction JSON. This is the standard pattern used in `tests/test_enrichment.py` and `tests/test_fact_extraction.py`.
- **Embeddings**: Use `MemoryStore` (in-memory store) with the default `LocalEmbedder` (ONNX, runs locally without network). Real embeddings ensure dedup testing is meaningful.
- **Store**: Use `SqliteStore(":memory:")` for integration tests (existing pattern).

## 12. Configuration

No new environment variables for Phase 1. The extraction uses existing config:
- `LORE_ENRICHMENT_ENABLED` — must be true for extraction to work
- `LORE_ENRICHMENT_MODEL` — model used for extraction LLM calls
- Dedup threshold (0.92) is a constructor parameter on `ConversationExtractor`

## 13. Files to Create

| File | Description |
|------|-------------|
| `src/lore/conversation/__init__.py` | Package init, exports `ConversationExtractor`, `ConversationJob` |
| `src/lore/conversation/extractor.py` | Main pipeline orchestrator |
| `src/lore/conversation/prompts.py` | Extraction prompt template |
| `src/lore/conversation/chunker.py` | Token-aware chunking |
| `src/lore/server/routes/conversations.py` | REST API endpoints (Phase 3) |
| `migrations/005_conversation_jobs.sql` | Server DB migration (Phase 3) |
| `tests/test_conversation_extractor.py` | Unit tests |
| `tests/test_conversation_chunker.py` | Chunker unit tests |
| `tests/test_conversation_integration.py` | Integration tests |
| `tests/test_cli_conversation.py` | CLI tests |

## 14. Files to Modify

| File | Changes |
|------|---------|
| `src/lore/types.py` | Add `ConversationMessage`, `ConversationJob` dataclasses |
| `src/lore/lore.py` | Add `add_conversation()`, `conversation_status()` methods |
| `src/lore/cli.py` | Add `add-conversation` subparser and `cmd_add_conversation()` |
| `src/lore/mcp/server.py` | Add `add_conversation` MCP tool |
| `src/lore/server/app.py` | Include conversations router (Phase 3) |
| `src/lore/server/models.py` | Add request/response models (Phase 3) |

## 15. Phased Implementation Mapping

### Phase 1 (P0): Core Pipeline + SDK + CLI
- Create `src/lore/conversation/` package (extractor, prompts, chunker)
- Add types to `types.py`
- Add `Lore.add_conversation()` to `lore.py`
- Add `add-conversation` CLI command
- Unit + integration tests

### Phase 2 (P0): MCP + User Scoping
- Add `add_conversation` MCP tool
- Add `user_id` parameter to `recall()` MCP tool + SDK
- Recall filtering tests

### Phase 3 (P1): REST API + Async
- Create `server/routes/conversations.py`
- Add `conversation_jobs` migration
- Background worker with `asyncio.create_task()`
- Register router in `app.py`

### Phase 4 (P1): Hardening
- Conversation chunking for long inputs
- Cost estimation in CLI output
- Error recovery (partial extraction on LLM failure)
- Documentation

## 16. Open Questions — Recommendations

1. **Conversation retention**: Store `messages_json` in `conversation_jobs` table (server mode only). Not stored in local/SQLite mode — keeps it simple, avoids storage bloat for single-user. Server mode retains for replay/re-extraction.

2. **Incremental extraction**: V1 treats each submission independently (per PRD). Track `session_id` in metadata for future dedup but don't enforce uniqueness.

3. **System messages**: Include them. They may contain useful context (tool definitions, persona instructions). The LLM extraction prompt handles noise filtering.

4. **Extraction quality feedback**: Existing `upvote_memory`/`downvote_memory` tools work on individual extracted memories. No changes needed for v1.
