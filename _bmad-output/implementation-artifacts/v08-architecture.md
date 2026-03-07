# Architecture: v0.8.0 -- Conversation Auto-Extract

**Version:** 2.0
**Author:** Solutions Architect
**Date:** 2026-03-07
**PRD:** `_bmad-output/planning-artifacts/v08-conversation-extract-prd.md`
**Depends on:** F2 (Fact Extraction), F3 (Consolidation), F6 (Metadata Enrichment), F9 (Dialog Classification)
**Dependents:** None

---

## 1. Overview

Conversation Auto-Extract accepts raw conversation messages (`{role, content}` pairs) and automatically extracts salient memories using LLM processing. It orchestrates existing infrastructure (F2, F3, F6, F9) behind a new pipeline exposed via four surfaces: REST API, MCP tool, CLI command, and Python SDK.

### Architecture Principles

1. **Orchestration, not reinvention** -- Composes existing components (`remember()`, enrichment, consolidation, fact extraction). No new AI capabilities.
2. **`remember()` is the exit** -- Every extracted memory is stored via `lore.remember()`, ensuring all downstream pipelines (enrichment, classification, graph extraction) fire automatically.
3. **Async by default, sync when local** -- Server mode returns 202 and processes in background. Local SDK mode runs synchronously.
4. **Cost-conscious LLM usage** -- Extraction is the only *new* LLM call. Enrichment, classification, fact extraction reuse existing pipelines triggered by `remember()`.
5. **Dedup before store** -- Candidates are deduplicated against existing store *before* calling `remember()`, avoiding redundant enrichment LLM calls on duplicates.
6. **No new dependencies** -- Uses existing `lore.enrichment.llm.LLMClient` (litellm), `LocalEmbedder`, and `asyncio` primitives.

---

## 2. Component Diagram

```
+------------------------------------------------------------------+
|                        CLIENT SURFACES                            |
|                                                                   |
|  +-----------+  +-------------+  +---------+  +---------------+  |
|  | REST API  |  |  MCP Tool   |  |   CLI   |  |  Python SDK   |  |
|  | POST /v1/ |  | add_conver- |  | lore    |  | lore.add_     |  |
|  | conversa- |  | sation()    |  | add-    |  | conversation()|  |
|  | tions     |  |             |  | conver- |  |               |  |
|  +-----+-----+  +------+------+  | sation  |  +-------+-------+  |
|        |               |         +----+----+          |           |
+--------|---------------|--------------|---------------|----------+
         |               |              |               |
         v               v              v               v
+------------------------------------------------------------------+
|                  CONVERSATION PROCESSOR                           |
|  (src/lore/conversation/processor.py)                            |
|                                                                   |
|  +------------------------------------------------------------+  |
|  |  1. VALIDATE       Check msg format, verify LLM available  |  |
|  +------------------------------------------------------------+  |
|  |  2. CONCATENATE     Build structured transcript from msgs   |  |
|  +------------------------------------------------------------+  |
|  |  3. CHUNK           Split if >8K tokens (msg-boundary,     |  |
|  |                     2-msg overlap between chunks)           |  |
|  +------------------------------------------------------------+  |
|  |  4. EXTRACT          LLM extraction per chunk               |  |
|  |     (src/lore/conversation/prompts.py)                      |  |
|  +------------------------------------------------------------+  |
|  |  5. DEDUPLICATE      a) intra-chunk (cosine >0.92)         |  |
|  |                      b) vs existing store (cosine >0.92)   |  |
|  |     (reuses: similarity logic from consolidation.py)        |  |
|  +------------------------------------------------------------+  |
|  |  6. STORE            lore.remember() per candidate          |  |
|  |     (triggers: enrichment F6, fact extraction F2, graph)    |  |
|  +------------------------------------------------------------+  |
|  |  7. COMPLETE         Return memory IDs + stats              |  |
|  +------------------------------------------------------------+  |
+------------------------------------------------------------------+
         |                                      |
         v                                      v
+-------------------+              +------------------------+
|  EXISTING INFRA   |              |  NEW: JOB TRACKING     |
|                   |              |  (server mode only)     |
|  - remember()     |              |  conversation_jobs      |
|  - EnrichmentPipe |              |  table in PostgreSQL    |
|  - FactExtraction |              +------------------------+
|  - Classification  |
|  - KnowledgeGraph  |
|  - Consolidation   |
+-------------------+
```

---

## 3. Data Flow Diagram

```
                     Input: messages[]
                     + user_id?, session_id?, project?
                            |
                            v
                   +------------------+
                   |    VALIDATE      |
                   |  - messages[]    |
                   |    non-empty     |
                   |  - each has      |
                   |    role+content  |
                   |  - LLM configured|
                   |  - cap at 500msg |
                   +--------+---------+
                            |
                            v
                   +------------------+
                   |   CONCATENATE    |
                   |  Build transcript|
                   |  [user]: ...     |
                   |  [assistant]: ...|
                   |  [system]: ...   |
                   +--------+---------+
                            |
                            v
                   +------------------+
                   |     CHUNK        |
                   |  if >8K tokens:  |
                   |  split at msg    |
                   |  boundaries with |
                   |  2-msg overlap   |
                   +--------+---------+
                            |
                     +------+------+
                     |             |
                     v             v
              +-----------+ +-----------+
              | EXTRACT   | | EXTRACT   |  (sequential per chunk)
              | chunk_1   | | chunk_2   |
              +-----------+ +-----------+
                     |             |
                     +------+------+
                            |
                            v
                   +------------------+
                   | INTRA-CHUNK DEDUP|
                   |  Embed each cand |
                   |  pairwise cosine |
                   |  merge if > 0.92 |
                   |  keep highest    |
                   |  confidence      |
                   +--------+---------+
                            |
                            v
                   +------------------+
                   |  STORE DEDUP     |
                   |  For each cand:  |
                   |  compare embed   |
                   |  vs existing mems|
                   |  skip if > 0.92  |
                   +--------+---------+
                            |
                    +-------+-------+
                    |               |
               [new memory]    [duplicate]
                    |               |
                    v               v
              +-----------+   (skip, count)
              |  STORE    |
              | remember()|
              | with meta:|
              | source=   |
              | "conver-  |
              |  sation"  |
              | user_id   |
              | session_id|
              +-----------+
                    |
                    v
           +----------------+
           |    RESULT      |
           | memory_ids[]   |
           | extracted: N   |
           | skipped: M     |
           | time_ms: T     |
           +----------------+
```

---

## 4. Component Design

### 4.1 ConversationProcessor

**File:** `src/lore/conversation/processor.py`

The orchestrator. Accepts raw messages, runs the full pipeline, returns results.

```python
@dataclass
class ConversationResult:
    """Result of conversation extraction."""
    job_id: str                    # ULID
    status: str                    # "completed" | "failed"
    message_count: int             # Input message count
    memories_extracted: int        # New memories stored
    memory_ids: List[str]          # ULIDs of new memories
    duplicates_skipped: int        # Candidates that matched existing
    processing_time_ms: int        # Total wall time
    error: Optional[str] = None   # Error message if failed


@dataclass
class ExtractionCandidate:
    """A candidate memory extracted by the LLM."""
    content: str
    type: str          # fact, preference, lesson, decision, correction
    confidence: float  # 0.0-1.0 from LLM
    tags: List[str]
    embedding: Optional[bytes] = None  # Computed during dedup phase


class ConversationProcessor:
    """Orchestrates conversation -> memory extraction pipeline."""

    def __init__(
        self,
        lore: "Lore",
        dedup_threshold: float = 0.92,
        max_chunk_tokens: int = 8000,
        chunk_overlap_messages: int = 2,
        min_confidence: float = 0.5,
    ) -> None:
        self._lore = lore
        self._extractor = ExtractionAgent(lore._enrichment_pipeline._llm)
        self._embedder = lore._embedder  # LocalEmbedder (384-dim ONNX)
        self._dedup_threshold = dedup_threshold
        self._max_chunk_tokens = max_chunk_tokens
        self._chunk_overlap = chunk_overlap_messages
        self._min_confidence = min_confidence

    def process(
        self,
        messages: List[Dict[str, str]],
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        project: Optional[str] = None,
    ) -> ConversationResult:
        """Run the full extraction pipeline synchronously.

        Steps: validate -> concatenate -> chunk -> extract ->
               dedup_intra -> dedup_store -> store -> result
        """
        ...

    def _validate(self, messages: List[Dict[str, str]]) -> None:
        """Validate message format and LLM availability.

        Raises:
            LoreError: If enrichment/LLM not configured.
            ValueError: If messages format invalid or empty.
        """
        ...

    def _concatenate(self, messages: List[Dict[str, str]]) -> str:
        """Build structured transcript string.

        Format:
            [user]\nUser: How do I deploy?\n\n[assistant]\nAssistant: Use copilot...
        """
        ...

    def _chunk(self, messages: List[Dict[str, str]]) -> List[List[Dict[str, str]]]:
        """Split messages into chunks if total exceeds max_chunk_tokens.

        Token estimate: len(content) / 3.5 (conservative).
        Split at message boundaries, never mid-message.
        Overlap: last chunk_overlap_messages from previous chunk.
        """
        ...

    def _dedup_candidates(
        self, candidates: List[ExtractionCandidate]
    ) -> List[ExtractionCandidate]:
        """Deduplicate candidates across chunks (intra-extraction).

        Embeds each candidate, pairwise cosine similarity,
        merges groups above threshold. Keeps highest confidence.
        """
        ...

    def _dedup_against_store(
        self,
        candidates: List[ExtractionCandidate],
        project: Optional[str],
        user_id: Optional[str] = None,
    ) -> Tuple[List[ExtractionCandidate], int]:
        """Deduplicate against existing memories (inter-extraction).

        Returns: (surviving_candidates, duplicates_skipped_count)
        """
        ...

    def _store_candidates(
        self,
        candidates: List[ExtractionCandidate],
        *,
        user_id: Optional[str],
        session_id: Optional[str],
        project: Optional[str],
    ) -> List[str]:
        """Store each candidate via lore.remember(). Returns memory IDs."""
        ...
```

**Key design decisions:**
- Takes a `Lore` instance -- leverages existing `remember()` pipeline for all downstream processing
- Dedup threshold 0.92 (lower than consolidation's 0.95) because conversation extraction produces noisier, more redundant candidates
- Chunking splits at message boundaries, never mid-message
- Sequential chunk extraction (not parallel) to avoid LLM rate limits
- Uses existing `LocalEmbedder` (384-dim ONNX) for dedup embeddings -- same embedder used by `remember()`

### 4.2 ExtractionAgent

**File:** `src/lore/conversation/prompts.py`

Wraps the LLM call that extracts candidate memories from a transcript. Uses the existing `LLMClient` from `src/lore/enrichment/llm.py` (litellm-based, supports OpenAI/Anthropic/Google).

```python
class ExtractionAgent:
    """LLM-based extraction of memories from conversation transcripts."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def extract(self, transcript: str) -> List[ExtractionCandidate]:
        """Extract candidate memories from a conversation transcript."""
        prompt = self._build_prompt(transcript)
        response = self._llm.complete(prompt, response_format={"type": "json_object"})
        return self._parse_response(response)

    def _build_prompt(self, transcript: str) -> str:
        """Build the extraction prompt using EXTRACTION_SYSTEM_PROMPT."""
        ...

    def _parse_response(self, response: str) -> List[ExtractionCandidate]:
        """Parse LLM JSON response. Best-effort with regex fallback.

        Same pattern as enrichment/pipeline.py:_parse_and_validate().
        """
        ...
```

### 4.3 Shared Similarity Utilities

**File:** `src/lore/similarity.py` (extracted from consolidation.py)

The consolidation engine's `_find_duplicates` method contains cosine similarity and Union-Find logic. We extract the core computation into a shared module used by both consolidation and conversation extraction.

```python
def deserialize_embedding(data: bytes) -> np.ndarray:
    """Unpack struct-packed float32 bytes into numpy array."""
    ...

def cosine_similarity_matrix(
    query_embeddings: np.ndarray,
    corpus_embeddings: np.ndarray,
) -> np.ndarray:
    """Pairwise cosine similarity between two sets of embeddings.

    Both inputs: shape (N, D) and (M, D). Returns (N, M) matrix.
    Uses normalized dot product (same as existing recall logic).
    """
    ...

def find_duplicate_groups(
    embeddings: List[bytes],
    threshold: float = 0.95,
) -> List[List[int]]:
    """Find groups of near-duplicate embeddings using Union-Find.

    Returns list of groups (each group is list of indices).
    Reuses Union-Find transitive closure from consolidation.py.
    """
    ...

def find_near_matches(
    query_embedding: bytes,
    corpus_embeddings: List[bytes],
    threshold: float = 0.92,
) -> List[int]:
    """Find corpus indices where similarity with query exceeds threshold."""
    ...
```

**Refactoring note:** `consolidation.py:_find_duplicates()` should be updated to call `similarity.find_duplicate_groups()` instead of its inline implementation. This is a safe refactor -- same logic, extracted.

### 4.4 User Memory Scoping

Not a separate class. User scoping is metadata-based:

**Storage:** `remember()` called with `metadata={"user_id": "alice", "session_id": "sess_abc", "source": "conversation", ...}`

**Local recall filter** (in `Lore._recall_local()`, after existing enrichment post-filter):
```python
if user_id is not None:
    results = [r for r in results
               if r.memory.metadata and r.memory.metadata.get("user_id") == user_id]
```

**Server recall filter** (in `routes/lessons.py`, SQL WHERE):
```sql
AND meta->>'user_id' = $user_id
```

**Dedup scoping:** During inter-extraction dedup, when `user_id` is set, only compare against memories with matching `user_id` or no `user_id` (global memories).

---

## 5. API Contracts

### 5.1 REST API: POST /v1/conversations

**Request:**
```http
POST /v1/conversations HTTP/1.1
Content-Type: application/json
Authorization: Bearer <api_key>

{
    "messages": [
        {"role": "user", "content": "How do I deploy to ECS?"},
        {"role": "assistant", "content": "Use copilot deploy..."},
        {"role": "user", "content": "That worked, but I had to set memory to 512MB"}
    ],
    "user_id": "alice",
    "session_id": "sess_abc123",
    "project": "my-project"
}
```

**Response (202 Accepted):**
```json
{
    "job_id": "01JEXAMPLE000000000000000",
    "status": "accepted",
    "message_count": 3
}
```

**Validation error (422):**
```json
{"detail": "messages must be a non-empty array of {role, content} objects"}
```

**LLM not configured (400):**
```json
{"detail": "Conversation extraction requires a configured LLM (enrichment_model)"}
```

### 5.2 REST API: GET /v1/conversations/{job_id}

**Response (processing):**
```json
{
    "job_id": "01JEXAMPLE000000000000000",
    "status": "processing",
    "message_count": 3
}
```

**Response (completed):**
```json
{
    "job_id": "01JEXAMPLE000000000000000",
    "status": "completed",
    "message_count": 3,
    "memories_extracted": 2,
    "memory_ids": ["01JABC000000000000000000", "01JDEF000000000000000000"],
    "duplicates_skipped": 1,
    "processing_time_ms": 3200
}
```

**Response (failed):**
```json
{
    "job_id": "01JEXAMPLE000000000000000",
    "status": "failed",
    "message_count": 3,
    "error": "LLM call failed: rate limit exceeded"
}
```

#### Server Route Implementation

**File:** `src/lore/server/routes/conversations.py`

```python
router = APIRouter(prefix="/v1/conversations", tags=["conversations"])

class ConversationRequest(BaseModel):
    messages: List[Dict[str, str]]
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    project: Optional[str] = None

class ConversationAcceptedResponse(BaseModel):
    job_id: str
    status: str = "accepted"
    message_count: int

class ConversationStatusResponse(BaseModel):
    job_id: str
    status: str
    message_count: int
    memories_extracted: Optional[int] = None
    memory_ids: Optional[List[str]] = None
    duplicates_skipped: Optional[int] = None
    processing_time_ms: Optional[int] = None
    error: Optional[str] = None

@router.post("", status_code=202, response_model=ConversationAcceptedResponse)
async def create_conversation(
    req: ConversationRequest,
    auth: AuthContext = Depends(require_auth),
):
    """Accept conversation for async extraction. Returns 202 with job_id."""
    # 1. Validate messages format
    # 2. Create job record in conversation_jobs (status=accepted)
    # 3. Enqueue for background processing
    # 4. Return 202 with job_id

@router.get("/{job_id}", response_model=ConversationStatusResponse)
async def get_conversation_status(
    job_id: str,
    auth: AuthContext = Depends(require_auth),
):
    """Poll job status. Returns current state + results when complete."""
    # SELECT from conversation_jobs WHERE id = job_id AND org_id = auth.org_id
```

**Background worker** (in server startup):
```python
async def _conversation_worker(pool, lore_instance, concurrency=2):
    """Background worker that processes conversation jobs FIFO.

    Uses asyncio.Semaphore(concurrency) to limit parallel extractions.
    Picks up jobs with status='accepted', transitions to 'processing',
    runs ConversationProcessor.process(), updates to 'completed'/'failed'.
    """
    ...
```

### 5.3 MCP Tool: add_conversation

**File:** `src/lore/mcp/server.py` (addition to existing 20 tools)

```python
@mcp.tool()
def add_conversation(
    messages: list[dict],
    user_id: str = None,
    session_id: str = None,
    project: str = None,
) -> str:
    """Accept raw conversation messages and automatically extract memories.

    Unlike 'remember' which requires pre-processed content, this tool accepts
    raw conversation history and uses LLM processing to identify and store
    salient facts, decisions, preferences, and lessons.

    Args:
        messages: List of {role: str, content: str} message objects.
                  Roles: "user", "assistant", "system", "tool".
        user_id: Optional user ID to scope extracted memories.
        session_id: Optional session ID for auditing.
        project: Optional project scope (defaults to configured project).

    Returns:
        Summary of extraction results.
    """
```

**Return format** (string, consistent with other MCP tools):
```
Processed 15 messages.
Extracted 4 memories, skipped 1 duplicate.

Memories:
  [01JABC...] (fact) ECS task memory limit should be 512MB
  [01JDEF...] (lesson) Deploy to staging before prod to catch memory issues
  [01JGHI...] (preference) Team prefers Fargate over EC2
  [01JKLM...] (decision) Using copilot for ECS deployments
```

### 5.4 Python SDK: lore.add_conversation()

**File:** `src/lore/lore.py` (addition to Lore class)

```python
def add_conversation(
    self,
    messages: List[Dict[str, str]],
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    project: Optional[str] = None,
) -> ConversationResult:
    """Accept raw conversation messages and extract memories.

    Requires enrichment=True (LLM must be configured).

    For local store: runs synchronously, returns completed result.
    For remote store (HttpStore): delegates to POST /v1/conversations,
    returns immediately with status="accepted". Poll with
    conversation_status(job_id).

    Args:
        messages: List of {"role": str, "content": str} dicts.
        user_id: Scope extracted memories to this user.
        session_id: Track conversation session in metadata.
        project: Project scope (defaults to self.project).

    Returns:
        ConversationResult with job_id, memory_ids, counts.

    Raises:
        LoreError: If enrichment/LLM is not configured.
        ValueError: If messages format is invalid.
    """

def conversation_status(self, job_id: str) -> ConversationResult:
    """Check status of an async conversation extraction job.

    Only relevant for remote store (HttpStore). Local store always
    returns completed results from add_conversation() directly.

    Raises:
        LoreError: If job not found.
    """
```

**Usage example:**
```python
from lore import Lore

lore = Lore(enrichment=True, enrichment_model="gpt-4o-mini")

# Local store -- synchronous, returns completed result
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

# Remote store -- async, returns job
lore_remote = Lore(store="remote", api_url="...", api_key="...")
job = lore_remote.add_conversation(messages=messages, user_id="alice")
print(job.status)  # "accepted"
status = lore_remote.conversation_status(job.job_id)
```

### 5.5 CLI: lore add-conversation

**File:** `src/lore/cli.py` (addition)

```
lore add-conversation --file conversation.json [--user-id USER] [--session-id SID] [--project P]
cat conversation.json | lore add-conversation [--user-id USER] [--session-id SID] [--project P]
```

**JSON file formats accepted:**
```json
{"messages": [{"role": "user", "content": "..."}, ...]}
```
or bare array:
```json
[{"role": "user", "content": "..."}, ...]
```

**Output:**
```
Accepted 15 messages for extraction.
Extracted 4 memories, skipped 1 duplicate.
Estimated cost: ~$0.002 (gpt-4o-mini)
Memory IDs:
  01JABC... (fact) ECS task memory limit should be 512MB
  01JDEF... (lesson) Deploy to staging before prod
  01JGHI... (preference) Team prefers Fargate over EC2
  01JKLM... (decision) Using copilot for ECS deployments
```

---

## 6. LLM Extraction Prompt

### 6.1 Prompt Design

The extraction prompt is the core intelligence. It must be high-precision (avoid noise), grounded (no hallucination), and correction-aware.

**File:** `src/lore/conversation/prompts.py`

```python
EXTRACTION_SYSTEM_PROMPT = """\
You are a memory extraction system. Your job is to read a conversation
and identify information worth remembering long-term.

Extract ONLY information that is:
- Concrete and actionable (not vague or obvious)
- Likely to be useful in future conversations
- Stated or confirmed by the participants (not speculative)

Do NOT extract:
- Greetings, pleasantries, or conversational filler
- Questions that were asked but not answered
- Information that was corrected later (extract the correction instead)
- Generic knowledge that any AI would already know
- Temporary/ephemeral information (meeting times, "I'll do it tomorrow")

For each memory, classify as one of:
- fact: Concrete piece of information ("ECS memory limit is 512MB")
- decision: A choice that was made ("We'll use Fargate instead of EC2")
- preference: User or team preference ("Alice prefers dark mode")
- lesson: Operational insight or learning ("Deploy to staging first")
- correction: When earlier information was wrong ("Actually, limit is 1GB not 512MB")

Return a JSON object:
{
    "memories": [
        {
            "content": "Clear, standalone statement of the memory",
            "type": "fact|decision|preference|lesson|correction",
            "confidence": 0.0-1.0,
            "tags": ["tag1", "tag2"]
        }
    ]
}

Rules for content:
- Each memory must be self-contained (understandable without conversation context)
- Rephrase into a clear declarative statement
- Include relevant specifics (names, numbers, tool names)
- If a correction, state the corrected fact, not the wrong one

Return {"memories": []} if nothing is worth extracting.\
"""

EXTRACTION_USER_PROMPT = """\
Extract memories from the following conversation:

<conversation>
{transcript}
</conversation>\
"""
```

### 6.2 What the LLM Sees

For input messages:
```json
[
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "How do I deploy to ECS?"},
    {"role": "assistant", "content": "Use copilot deploy..."},
    {"role": "user", "content": "That worked, but I had to set memory to 512MB"}
]
```

The transcript becomes:
```
[system]
System: You are a helpful assistant.

[user]
User: How do I deploy to ECS?

[assistant]
Assistant: Use copilot deploy...

[user]
User: That worked, but I had to set memory to 512MB
```

### 6.3 System Message Handling

System messages are **included** in the transcript for context (tool definitions, persona instructions help the LLM understand what was discussed). The extraction prompt's "Do NOT extract" rules prevent extracting system prompt content as memories.

### 6.4 Expected LLM Response

```json
{
    "memories": [
        {
            "content": "ECS task memory limit should be set to 512MB for deployment",
            "type": "fact",
            "confidence": 0.9,
            "tags": ["ecs", "deployment", "memory-limit"]
        },
        {
            "content": "Use AWS Copilot 'copilot deploy' command for ECS deployments",
            "type": "fact",
            "confidence": 0.85,
            "tags": ["ecs", "copilot", "deployment"]
        }
    ]
}
```

### 6.5 Chunking Strategy

When a conversation exceeds `max_chunk_tokens` (default 8000):

1. **Estimate tokens per message:** `len(content) / 3.5` (conservative, errs on smaller chunks)
2. **Split at message boundaries** -- never split a message mid-content
3. **Overlap:** Include the last `chunk_overlap_messages` (default 2) messages from the previous chunk as context prefix
4. **Each chunk** gets its own LLM extraction call with the full system prompt
5. **Merge** results from all chunks, then deduplicate intra-extraction before deduplicating against store

---

## 7. Deduplication Strategy

### 7.1 Two-Phase Dedup

**Phase 1: Intra-extraction (across chunks)**
- After all chunks are extracted, embed each candidate using `LocalEmbedder`
- Pairwise cosine similarity within the candidate set
- If similarity > 0.92: keep the one with higher confidence, discard the other
- Catches the same fact extracted from overlapping chunk context

**Phase 2: Inter-extraction (against store)**
- For each surviving candidate, compare against existing memories
- Load existing memory embeddings via `lore.list_memories(project=project)`
- If `user_id` is set, additionally filter to same user's memories + global (no user_id)
- If any existing memory has cosine similarity > 0.92 with the candidate, skip it
- This prevents storing "ECS limit is 512MB" when it already exists

### 7.2 Configuration

```python
DEFAULT_CONVERSATION_CONFIG = {
    "dedup_threshold": 0.92,          # Cosine similarity threshold
    "max_chunk_tokens": 8000,         # Token limit per LLM call
    "chunk_overlap_messages": 2,      # Context overlap between chunks
    "max_candidates_per_chunk": 20,   # Safety limit on LLM output
    "min_confidence": 0.5,            # Discard low-confidence candidates
}
```

### 7.3 Why 0.92 (not 0.95)?

Consolidation uses 0.95 for manually-created memories (high precision -- don't merge intentionally distinct memories). Conversation extraction is noisier:
- Same information phrased differently across chunks
- Users may submit overlapping conversations
- False negatives (storing a duplicate) are worse than false positives (skipping useful) in automated extraction context
- Existing consolidation (F3) catches remaining dupes on schedule

### 7.4 Performance for Large Stores

For stores with >10K memories, loading all embeddings for comparison is expensive.

**Optimizations:**
- Only load memories from the **last 30 days** for dedup comparison (configurable `dedup_lookback_days`)
- If `user_id` is set, only load that user's memories (typically much smaller set)
- Use **vectorized numpy** operations (already used in `recall()`)
- Future enhancement: use pgvector ANN index for server mode (`ORDER BY embedding <=> $query LIMIT 1` with distance check)

---

## 8. Database Schema Changes

### 8.1 New Table: conversation_jobs (server mode only)

**Migration:** `migrations/008_conversations.sql`

```sql
-- 008_conversations.sql: Conversation auto-extract job tracking

CREATE TABLE IF NOT EXISTS conversation_jobs (
    id                  TEXT PRIMARY KEY,                -- ULID
    org_id              UUID NOT NULL REFERENCES orgs(id),
    status              TEXT NOT NULL DEFAULT 'accepted'
                        CHECK (status IN ('accepted', 'processing', 'completed', 'failed')),
    message_count       INTEGER NOT NULL,
    messages_json       TEXT NOT NULL,                   -- Stored conversation payload
    user_id             TEXT,                            -- Optional user scope
    session_id          TEXT,                            -- Optional session ID
    project             TEXT,                            -- Project scope
    memory_ids          JSONB DEFAULT '[]'::jsonb,       -- Array of extracted memory ULIDs
    memories_extracted  INTEGER DEFAULT 0,
    duplicates_skipped  INTEGER DEFAULT 0,
    error               TEXT,                            -- Error message if failed
    processing_time_ms  INTEGER,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ
);

-- Index for listing jobs by org
CREATE INDEX IF NOT EXISTS idx_conversation_jobs_org
    ON conversation_jobs(org_id);

-- Index for worker: find pending jobs
CREATE INDEX IF NOT EXISTS idx_conversation_jobs_pending
    ON conversation_jobs(org_id, status)
    WHERE status IN ('accepted', 'processing');

-- Index for user-scoped job lookup
CREATE INDEX IF NOT EXISTS idx_conversation_jobs_user
    ON conversation_jobs(org_id, user_id)
    WHERE user_id IS NOT NULL;

-- Index on lessons.meta->>'user_id' for recall filtering
CREATE INDEX IF NOT EXISTS idx_lessons_user_id
    ON lessons((meta->>'user_id'))
    WHERE meta->>'user_id' IS NOT NULL;
```

### 8.2 No Changes to Existing Tables

Extracted memories are stored via `remember()` into the existing `lessons` table. User scoping uses the existing `meta` JSONB column:

```sql
-- Server-side recall with user_id filter
SELECT * FROM lessons
WHERE org_id = $1
  AND (meta->>'user_id' = $2 OR meta->>'user_id' IS NULL)
ORDER BY ...
```

The only addition to the `lessons` table is the new **index** on `meta->>'user_id'` (in migration 008).

### 8.3 SQLite (Local Mode)

No schema changes. No job tracking table -- local mode is synchronous.

User filtering in local `recall()` is a Python-side post-filter on `memory.metadata.get("user_id")`.

---

## 9. Sequence Diagrams

### 9.1 Local Mode (SDK / MCP / CLI)

```
Client              Lore              ConvProcessor       ExtractionAgent    Store
  |                  |                      |                    |              |
  |--add_conversation(msgs, user_id)------->|                    |              |
  |                  |                      |                    |              |
  |                  |                      |--validate()        |              |
  |                  |                      |--concatenate()     |              |
  |                  |                      |--chunk()           |              |
  |                  |                      |                    |              |
  |                  |                      |---extract(chunk)-->|              |
  |                  |                      |   [LLM call via    |              |
  |                  |                      |    litellm]        |              |
  |                  |                      |<--candidates[]-----|              |
  |                  |                      |                    |              |
  |                  |                      |--embed candidates  |              |
  |                  |                      |  (LocalEmbedder)   |              |
  |                  |                      |                    |              |
  |                  |                      |--dedup intra       |              |
  |                  |                      |  (cosine > 0.92)   |              |
  |                  |                      |                    |              |
  |                  |                      |--dedup vs store----|------------>|
  |                  |                      |                list_memories()   |
  |                  |                      |                <--memories[]-----|
  |                  |                      |  (cosine filter)   |              |
  |                  |                      |                    |              |
  |                  |                      |--store via remember()             |
  |                  |  remember(c1, meta)  |                    |   save()-->|
  |                  |  remember(c2, meta)  |                    |   save()-->|
  |                  |                      |                    |              |
  |<--ConversationResult(memory_ids, stats)-|                    |              |
```

### 9.2 Server Mode (REST API)

```
Client         API Server       PostgreSQL       Background Worker    Lore
  |                |                |                   |               |
  |--POST /v1/conversations-------->|                   |               |
  |  {messages, user_id}   |       |                   |               |
  |                |--validate----->|                   |               |
  |                |--INSERT job--->|                   |               |
  |                |  (status=      |                   |               |
  |                |   accepted)    |                   |               |
  |<--202 {job_id}-|                |                   |               |
  |                |                |                   |               |
  |                |          [worker loop]             |               |
  |                |                |<--SELECT pending--|               |
  |                |                |--UPDATE processing>               |
  |                |                |                   |               |
  |                |                |                   |--process()-->|
  |                |                |                   | (full pipeline|
  |                |                |                   |  as 9.1)     |
  |                |                |                   |<--result-----|
  |                |                |                   |               |
  |                |                |<--UPDATE completed-|              |
  |                |                | (memory_ids, stats)|              |
  |                |                |                   |               |
  |--GET /v1/conversations/{job_id}>|                   |               |
  |                |--SELECT job--->|                   |               |
  |<--200 {status: "completed", ...}|                   |               |
```

---

## 10. Backwards Compatibility

### 10.1 Zero Breaking Changes

| Component | Impact |
|-----------|--------|
| `remember()` | Unchanged. |
| `recall()` | Gains optional `user_id` param. Without it, behavior identical. |
| MCP tools | All 20 existing tools unchanged. `add_conversation` is additive. |
| CLI | All existing commands unchanged. `add-conversation` is additive. |
| REST API | All existing endpoints unchanged. `/v1/conversations` is additive. |
| Database | New migration adds `conversation_jobs` table + index. No existing table mods. |
| Store ABC | No changes to `Store` abstract base class. |
| Configuration | New optional config keys. All defaults preserve existing behavior. |

### 10.2 LLM Requirement

`add_conversation` requires `enrichment=True` (LLM configured). Clear errors everywhere:
- SDK: raises `LoreError("Conversation extraction requires enrichment=True")`
- MCP: returns error string
- CLI: prints error, exits code 1
- REST: returns 400 with clear message

Existing features that don't use LLM continue to work without one.

### 10.3 recall() User Scoping

Purely additive:
- `recall("query")` -- returns all memories (unchanged)
- `recall("query", user_id="alice")` -- returns only Alice's memories + global memories (no user_id)

Memories stored without `user_id` in metadata are global (returned regardless of filter).

---

## 11. Deployment Topology

### 11.1 Local Mode (stdio MCP / SDK / CLI)

```
+-------------------------------------------+
|              User Machine                  |
|                                            |
|  +--------+     +-------+     +---------+  |
|  | Claude |---->|  MCP  |---->|  Lore   |  |
|  | (agent)|     | stdio |     | Library |  |
|  +--------+     +-------+     +----+----+  |
|                                    |        |
|  +--------+                   +----+----+   |
|  |  CLI   |--(same process)-->| SQLite  |   |
|  +--------+                   | Store   |   |
|                               +---------+   |
|  +--------+                                 |
|  | Python |--(same process)-->  (as above)  |
|  |  App   |                                 |
|  +--------+                                 |
|                                             |
|  LLM calls: outbound HTTPS to              |
|  OpenAI / Anthropic / Google API            |
+-------------------------------------------+
```

- All processing synchronous (single process)
- SQLite store, no background workers
- LLM calls are the only external dependency
- `add_conversation()` blocks until extraction completes
- No `conversation_jobs` table needed

### 11.2 Cloud Mode (HTTP API)

```
+-------------------------------------------+
|              Cloud / Self-Hosted           |
|                                            |
|  +--------+     +-----------+              |
|  | Client |---->| FastAPI   |              |
|  | (REST) |     | Server    |              |
|  +--------+     +-----+-----+             |
|                       |                    |
|                 +-----+-----+              |
|                 | Background |              |
|                 | Workers    |              |
|                 | (asyncio   |              |
|                 |  tasks)    |              |
|                 +-----+-----+              |
|                       |                    |
|                 +-----+-----+              |
|                 | PostgreSQL |              |
|                 | + pgvector |              |
|                 +-----------+              |
|                                            |
|  LLM calls: outbound HTTPS to             |
|  OpenAI / Anthropic / Google API           |
+-------------------------------------------+
```

- Async processing via asyncio background tasks
- PostgreSQL with pgvector for storage + job tracking
- Configurable worker concurrency (default: 2, env `LORE_CONVERSATION_WORKERS`)
- Job timeout: 5 min (env `LORE_CONVERSATION_TIMEOUT`)
- FIFO job ordering

### 11.3 Hybrid Mode (SDK -> Remote)

```
+------------------+          +------------------+
|  User Machine    |          |  Cloud Server    |
|                  |  HTTPS   |                  |
|  +--------+      +--------->  +----------+    |
|  | Python |      |          |  | FastAPI  |    |
|  | App    |      |          |  | Server   |    |
|  | (SDK   |      |          |  +----------+    |
|  |  with  |      |          |  | Workers  |    |
|  | HttpStore)    |          |  +----------+    |
|  +--------+      |          |  | Postgres |    |
+------------------+          +------------------+
```

- SDK with `store="remote"` delegates `add_conversation()` to `POST /v1/conversations`
- Returns immediately with `job_id` + `status="accepted"`
- Client polls `conversation_status(job_id)` for result

---

## 12. Configuration

### 12.1 New Lore.__init__() Parameters

```python
# Conversation extraction (new in v0.8.0)
conversation_dedup_threshold: float = 0.92,       # Cosine sim threshold for dedup
conversation_max_chunk_tokens: int = 8000,         # Max tokens per LLM extraction call
conversation_chunk_overlap: int = 2,               # Message overlap between chunks
conversation_min_confidence: float = 0.5,          # Discard below this confidence
```

### 12.2 Server Environment Variables

```
LORE_CONVERSATION_WORKERS=2           # Concurrent extraction workers
LORE_CONVERSATION_TIMEOUT=300         # Job timeout in seconds (5 min)
LORE_CONVERSATION_RETAIN=true         # Store raw messages in job table
```

---

## 13. File Structure

New and modified files for v0.8.0:

```
src/lore/
+-- conversation/                     # NEW PACKAGE
|   +-- __init__.py                   # Exports ConversationProcessor, ConversationResult
|   +-- processor.py                  # ConversationProcessor class
|   +-- prompts.py                    # ExtractionAgent, EXTRACTION_SYSTEM_PROMPT
+-- similarity.py                     # NEW: Shared cosine similarity utilities
|                                     # (extracted from consolidation.py)
+-- lore.py                           # MODIFIED: +add_conversation(), +conversation_status()
|                                     #           recall() gains user_id param
+-- types.py                          # MODIFIED: +ConversationResult, +ExtractionCandidate
+-- mcp/
|   +-- server.py                     # MODIFIED: +add_conversation tool
+-- cli.py                            # MODIFIED: +add-conversation command
+-- consolidation.py                  # MODIFIED: refactor to use similarity.py
+-- server/
    +-- routes/
        +-- conversations.py          # NEW: POST/GET /v1/conversations
        +-- __init__.py               # MODIFIED: register conversations router

migrations/
+-- 008_conversations.sql             # NEW: conversation_jobs table + user_id index

tests/
+-- test_conversation.py              # NEW: Unit tests for processor, extraction, dedup
+-- test_conversation_cli.py          # NEW: CLI integration tests
+-- server/
    +-- test_conversations.py         # NEW: Server route tests
```

---

## 14. Risk Analysis and Mitigation

### 14.1 Technical Risks

| Risk | Severity | Likelihood | Mitigation |
|------|----------|------------|------------|
| **LLM returns malformed JSON** | Medium | High | Best-effort parsing with regex fallback (same pattern as `enrichment/pipeline.py:_parse_and_validate()`). Discard unparseable candidates, don't fail whole job. |
| **LLM hallucinates facts** | High | Medium | Prompt: "extract ONLY information stated in the conversation". Confidence threshold 0.5 filters low-confidence. Users can downvote bad memories via existing tools. |
| **Dedup false positives (useful memory skipped)** | Medium | Medium | Threshold 0.92 tuned via benchmarking. Configurable per deployment. Re-submission re-extracts. |
| **Dedup false negatives (duplicate stored)** | Low | Medium | Acceptable -- existing F3 consolidation catches remaining dupes on schedule. |
| **Long conversations exhaust context** | Medium | Low | Chunking at 8K tokens. Max 500 messages validation. Each chunk independent LLM call. |
| **LLM cost surprise** | Medium | Medium | CLI displays cost estimate. Documentation states LLM requirement. Cost ~$0.001/20-msg conversation with gpt-4o-mini. |
| **Slow dedup for large stores** | Medium | Medium | Time-bounded dedup (last 30 days). User-scoped dedup reduces set. Vectorized numpy. Future: pgvector ANN. |
| **Concurrent job starvation (server)** | Low | Low | Worker concurrency limit (default 2). FIFO ordering. Job timeout (5 min). Dead job cleanup on startup. |

### 14.2 Product Risks

| Risk | Severity | Likelihood | Mitigation |
|------|----------|------------|------------|
| **Low extraction quality** | High | Medium | Manual review of 50 runs (per PRD metrics). Iterate prompt. Expose confidence. min_confidence=0.5 default. |
| **Confusion: remember vs add_conversation** | Medium | Medium | Clear doc/description differentiation. MCP tool description explicit. |
| **Privacy: storing raw conversations** | High | Low | Local mode: messages discarded after processing. Server mode: stored in `conversation_jobs` for re-extraction (configurable via `LORE_CONVERSATION_RETAIN`). |

### 14.3 Open Questions Resolution

| Question (from PRD) | Architecture Answer |
|---------------------|---------------------|
| **Conversation retention** | Server mode: stored in `conversation_jobs.messages_json`. Local mode: discarded after processing. Configurable. |
| **Incremental extraction** | V1: each submission independent. Architecture supports future incremental via `session_id` lookup in `conversation_jobs`. |
| **System messages** | Included in transcript for context. Extraction prompt's "Do NOT extract" rules prevent noise. |
| **Extraction quality feedback** | Existing `upvote_memory`/`downvote_memory` works on extracted memories. No architecture change needed. |

---

## 15. Phase Mapping

| Phase | PRD Section | Components | Files |
|-------|-------------|-----------|-------|
| **P1: Core Pipeline** | Phase 1 | ConversationProcessor, ExtractionAgent, similarity utils, SDK method, CLI | `conversation/`, `similarity.py`, `lore.py`, `cli.py`, `types.py` |
| **P2: MCP + Scoping** | Phase 2 | MCP tool, user_id on recall, metadata filtering | `mcp/server.py`, `lore.py` recall changes |
| **P3: REST + Async** | Phase 3 | Server routes, job table, background workers, migration | `server/routes/conversations.py`, `migrations/008_conversations.sql` |
| **P4: Hardening** | Phase 4 | Chunking edge cases, cost tracking, error recovery, docs | `conversation/processor.py`, CLI output, documentation |
