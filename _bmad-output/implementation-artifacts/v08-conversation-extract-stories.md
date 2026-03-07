# v0.8.0: Conversation Auto-Extract — User Stories

---

## Phase 1: Core Extraction Pipeline

---

## S1: ConversationMessage and ConversationJob Data Types

**As a** developer
**I want to** have `ConversationMessage` and `ConversationJob` dataclasses in `types.py`
**So that** the conversation pipeline has well-defined data contracts

**Acceptance Criteria:**
- `ConversationMessage` dataclass with `role: str` and `content: str` fields
- `ConversationJob` dataclass with `job_id`, `status`, `message_count`, `memories_extracted`, `memory_ids`, `duplicates_skipped`, `processing_time_ms`, `error` fields
- Status values: `accepted`, `processing`, `completed`, `failed`
- Follows existing dataclass patterns in `types.py` (`Memory`, `Fact`, `ConsolidationResult`)
- Both types exported from `conversation/__init__.py`

**Estimate:** S

---

## S2: Extraction Prompt Template

**As a** developer
**I want to** have an LLM prompt that extracts salient memories from a conversation transcript
**So that** the extraction step produces structured, high-quality candidates

**Acceptance Criteria:**
- Prompt template in `src/lore/conversation/prompts.py`
- Instructs LLM to extract: facts, decisions, preferences, lessons, corrections
- Instructs LLM to skip greetings, acknowledgments, trivial exchanges
- Requires self-contained memories (understandable without conversation context)
- Requests confidence score (0.0–1.0) and tags (1–5 per memory)
- Output format: `{"memories": [{"content", "type", "confidence", "tags"}]}`
- `{transcript}` placeholder for conversation text

**Estimate:** S

---

## S3: ConversationExtractor — Core Pipeline

**As a** developer
**I want to** have a `ConversationExtractor` class that orchestrates extract → dedup → store
**So that** raw conversations are converted into deduplicated, enriched memories

**Acceptance Criteria:**
- Class in `src/lore/conversation/extractor.py`
- Constructor takes `Lore` instance and optional `dedup_threshold` (default 0.92)
- `extract(messages, user_id, session_id, project)` method runs the full pipeline
- Pipeline stages: validate → concatenate → extract (LLM) → dedup → store → return
- Validation: raises `ValueError` for empty messages, `RuntimeError` if no LLM configured
- Concatenation: formats messages as `[role]: content` separated by double newlines
- LLM call uses `enrichment_pipeline.llm.complete()` — no new LLM infrastructure
- Dedup: calls `lore.recall(candidate_content, limit=3)`, skips if any result has `score >= 0.92`
- Store: calls `lore.remember()` for each unique candidate with `source="conversation"` metadata
- Type mapping: `fact→fact`, `decision→general`, `preference→preference`, `lesson→lesson`, `correction→general`
- Returns `ConversationJob` with `status="completed"`, memory IDs, and counts
- Malformed LLM JSON returns empty list (no crash), logged as warning

**Estimate:** L

---

## S4: LLM Response Parsing

**As a** developer
**I want to** robustly parse LLM extraction responses
**So that** malformed or wrapped JSON doesn't crash the pipeline

**Acceptance Criteria:**
- `_parse_extraction_response(response)` method on `ConversationExtractor`
- Strips markdown code block wrappers (``` json ... ```)
- Returns empty list on `JSONDecodeError` (logs warning, no crash)
- Validates each candidate has non-empty `content` field
- Clamps confidence to [0.0, 1.0]
- Limits tags to 5 per memory, lowercased
- Defaults: type → `"general"`, confidence → `0.8`, tags → `[]`

**Estimate:** S

---

## S5: SDK Method `Lore.add_conversation()`

**As a** Python developer integrating Lore
**I want to** call `lore.add_conversation(messages, user_id=..., session_id=...)`
**So that** I can programmatically feed conversations and get memories extracted

**Acceptance Criteria:**
- Method in `src/lore/lore.py`
- Signature: `add_conversation(messages, *, user_id, session_id, project) -> ConversationJob`
- Accepts `messages` as `List[Dict[str, str]]` with `role` and `content` keys
- For local store: runs extraction synchronously via `ConversationExtractor`
- For remote store: delegates to `POST /v1/conversations` (returns accepted job)
- Raises `RuntimeError` if enrichment/LLM not configured
- Raises `ValueError` if messages is empty
- `conversation_status(job_id)` method for remote store job polling

**Estimate:** M

---

## S6: CLI Command `lore add-conversation`

**As a** developer reviewing conversation logs
**I want to** run `lore add-conversation --file conversation.json` or pipe from stdin
**So that** I can bulk-import knowledge from past sessions

**Acceptance Criteria:**
- Subcommand `add-conversation` registered in `cli.py` `build_parser()`
- `--file` / `-f` flag reads JSON from a file path
- Reads from stdin when input is piped (`not sys.stdin.isatty()`)
- Accepts both `{"messages": [...]}` and bare `[{...}]` JSON formats
- `--user-id`, `--session-id`, `--project` flags
- `--db` flag for SQLite path (existing pattern)
- Output: message count, extracted memory count, skipped duplicates, memory IDs
- Error: prints to stderr and exits 1 if no file and no stdin, or invalid JSON

**Estimate:** M

---

## S7: Unit Tests — Extractor and Parsing

**As a** developer
**I want to** comprehensive unit tests for the extraction pipeline
**So that** parsing, dedup, and type mapping are verified

**Acceptance Criteria:**
- `tests/test_conversation_extractor.py`
- Tests: `test_format_transcript` — messages formatted as `[role]: content`
- Tests: `test_parse_extraction_valid` — valid JSON parsed correctly
- Tests: `test_parse_extraction_malformed` — bad JSON returns `[]`, no crash
- Tests: `test_parse_extraction_markdown_wrapped` — JSON in code blocks parsed
- Tests: `test_type_mapping` — all 5 LLM types mapped to valid memory types
- Tests: `test_confidence_clamping` — out-of-range values clamped
- Tests: `test_empty_messages_raises` — `ValueError` for empty list
- Tests: `test_no_llm_raises` — `RuntimeError` with clear message
- LLM calls mocked via `enrichment_pipeline.llm.complete()`

**Estimate:** M

---

## S8: Integration Test — End-to-End Extraction

**As a** developer
**I want to** an integration test proving conversations produce recallable memories
**So that** the full pipeline is verified

**Acceptance Criteria:**
- `tests/test_conversation_integration.py`
- Test: `test_end_to_end_extract` — messages in → memories stored
- Test: `test_extracted_memories_recallable` — `recall()` finds auto-extracted memories
- Test: `test_metadata_persisted` — `source=conversation`, `user_id`, `session_id` in metadata
- Test: `test_dedup_across_conversations` — second extraction of same conversation yields 0 new memories
- Uses `SqliteStore(":memory:")` with real `LocalEmbedder`
- LLM mocked to return canned extraction JSON

**Estimate:** M

---

## Phase 2: MCP + User Scoping

---

## S9: MCP Tool `add_conversation`

**As an** AI agent using Lore via MCP
**I want to** call `add_conversation` to dump my conversation context
**So that** Lore extracts and stores relevant knowledge automatically

**Acceptance Criteria:**
- Tool in `src/lore/mcp/server.py`
- Parameters: `messages: list[dict]`, `user_id: str = None`, `session_id: str = None`, `project: str = None`
- Description explains difference from `remember` (raw input vs pre-processed)
- Delegates to `lore.add_conversation()`
- Returns formatted string: extracted count, duplicates skipped, memory IDs
- Catches `RuntimeError` (no LLM) and returns clear error string
- Works with local SQLite store (no server required)

**Estimate:** S

---

## S10: User ID and Session ID Metadata on Extracted Memories

**As an** application serving multiple users
**I want to** extracted memories tagged with `user_id` and `session_id`
**So that** memories can be scoped and audited per user/session

**Acceptance Criteria:**
- `user_id` stored in `metadata.user_id` on each extracted memory
- `session_id` stored in `metadata.session_id` on each extracted memory
- `extracted_at` ISO 8601 timestamp in metadata
- `extraction_model` (LLM model name) in metadata
- `conversation_length` (message count) in metadata
- Memories without `user_id` remain global (no scoping applied)

**Estimate:** S

---

## S11: Recall Filtering by `user_id`

**As an** application developer
**I want to** call `recall(query, user_id="alice")` and only get Alice's memories
**So that** each user's recalled memories are isolated

**Acceptance Criteria:**
- `recall()` SDK method gains optional `user_id` parameter
- `recall` MCP tool gains optional `user_id` parameter
- When `user_id` is set: only memories with matching `metadata.user_id` are returned
- When `user_id` is not set: all memories returned (backwards compatible)
- `session_id` stored for auditing but does NOT affect recall filtering
- Test: `recall(query, user_id="alice")` returns only Alice's memories
- Test: `recall(query)` returns all memories regardless of `user_id`

**Estimate:** M

---

## Phase 3: REST API + Async Processing

---

## S12: `conversation_jobs` Database Table and Migration

**As a** developer
**I want to** a `conversation_jobs` table for tracking async extraction jobs
**So that** the server can accept conversations and report status

**Acceptance Criteria:**
- Migration file: `migrations/005_conversation_jobs.sql`
- Columns: `id` (TEXT PK, ULID), `org_id` (TEXT FK → orgs), `status`, `message_count`, `messages_json`, `user_id`, `session_id`, `project`, `memory_ids` (JSON array), `memories_extracted`, `duplicates_skipped`, `error`, `processing_time_ms`, `created_at`, `completed_at`
- Default status: `accepted`
- Indexes on `org_id` and `status`
- Migration is idempotent (`CREATE TABLE IF NOT EXISTS`)

**Estimate:** S

---

## S13: POST `/v1/conversations` Endpoint

**As an** application developer
**I want to** POST conversation messages to `/v1/conversations` and get a job ID back
**So that** I can submit conversations for async extraction

**Acceptance Criteria:**
- Route in `src/lore/server/routes/conversations.py`
- Request body: `{"messages": [{role, content}], "user_id", "session_id", "project"}`
- Validates: messages non-empty, each message has `role` and `content`
- Returns `202 Accepted` with `{job_id, status: "accepted", message_count}`
- Inserts job record into `conversation_jobs` table
- Dispatches background task via `asyncio.create_task()`
- Requires `writer` or `admin` role (existing auth pattern)
- Request/response models in `server/models.py`

**Estimate:** M

---

## S14: GET `/v1/conversations/{job_id}` Status Endpoint

**As an** application developer
**I want to** poll job status via `GET /v1/conversations/{job_id}`
**So that** I know when extraction is complete and can get the memory IDs

**Acceptance Criteria:**
- Returns `{job_id, status, message_count, memories_extracted, memory_ids, duplicates_skipped, processing_time_ms, error}`
- Status transitions: `accepted → processing → completed` or `accepted → processing → failed`
- Returns `404` if job ID not found or belongs to different org
- Org-scoped: only returns jobs belonging to the authenticated org

**Estimate:** S

---

## S15: Background Worker for Async Extraction

**As a** server operator
**I want to** extraction jobs to process in the background after acceptance
**So that** callers aren't blocked by LLM processing time

**Acceptance Criteria:**
- Background task updates job status to `processing` before starting
- Creates `ConversationExtractor` and runs `extract()` on the stored messages
- On success: updates status to `completed`, records `memory_ids`, `memories_extracted`, `duplicates_skipped`, `processing_time_ms`, `completed_at`
- On failure: updates status to `failed`, records `error` message and `processing_time_ms`
- Uses `asyncio.create_task()` (matches existing `ConsolidationScheduler` pattern)
- Router registered in `server/app.py`

**Estimate:** M

---

## S16: Server Integration Tests

**As a** developer
**I want to** integration tests for the conversation REST API
**So that** the async pipeline is verified end-to-end

**Acceptance Criteria:**
- Test: POST returns 202 with valid job_id
- Test: GET returns job status progressing through accepted → completed
- Test: Extracted memories appear in recall after job completes
- Test: POST with empty messages returns 400
- Test: POST with missing role/content returns 400
- Test: GET with invalid job_id returns 404
- Test: Multiple concurrent jobs process correctly
- LLM mocked, real database (test PostgreSQL or SQLite)

**Estimate:** M

---

## Phase 4: Hardening + Documentation

---

## S17: Token-Aware Conversation Chunking

**As a** developer
**I want to** long conversations automatically split into chunks that fit the LLM context window
**So that** conversations of any length can be processed

**Acceptance Criteria:**
- `ConversationChunker` class in `src/lore/conversation/chunker.py`
- Constructor: `max_tokens=8000`, `overlap_messages=2`
- `chunk(messages)` returns `List[List[ConversationMessage]]`
- Token estimation: `len(text.split()) / 0.75` (no tokenizer dependency)
- Short conversations (<8K tokens): single chunk returned
- Long conversations: split at message boundaries, last 2 messages overlap into next chunk
- Single huge message (>8K tokens): returned as its own chunk (not split mid-message)
- Unit tests in `tests/test_conversation_chunker.py`: short no-chunk, long multi-chunk, overlap verification, single huge message

**Estimate:** M

---

## S18: Cost Estimation in CLI Output

**As a** developer using the CLI
**I want to** see estimated LLM cost after extraction
**So that** I understand the cost of processing conversations

**Acceptance Criteria:**
- CLI output includes line: `Estimated cost: ~$X.XXX (N tokens, model-name)`
- Cost calculated from input token count and model pricing
- Token count estimated from transcript length
- Displayed after extraction completes (informational, not blocking)
- Does not require new dependencies

**Estimate:** S

---

## S19: Error Recovery — Partial Extraction on LLM Failure

**As a** developer
**I want to** successfully extracted memories preserved when a chunk's LLM call fails
**So that** partial results aren't lost due to a single failure

**Acceptance Criteria:**
- Multi-chunk extraction: if one chunk's LLM call fails, other chunks' results are still stored
- `ConversationJob` reports `memories_extracted` for whatever succeeded
- `ConversationJob.error` contains failure details for failed chunks
- Single-chunk failure: raises `RuntimeError` to caller
- Server mode: job status set to `completed` (not `failed`) if at least some memories extracted, with error details included
- Test: 3-chunk conversation with middle chunk failing → memories from chunks 1 and 3 stored

**Estimate:** M

---

## S20: API Reference Documentation

**As a** developer integrating Lore
**I want to** clear API documentation for the conversation extract feature
**So that** I can quickly understand and use all integration surfaces

**Acceptance Criteria:**
- Documents REST API: POST/GET endpoints with request/response examples
- Documents MCP tool: `add_conversation` parameters and return format
- Documents CLI: `lore add-conversation` with all flags and examples
- Documents SDK: `lore.add_conversation()` and `lore.conversation_status()` with code examples
- Documents JSON input formats: `{"messages": [...]}` and bare `[...]`
- Documents `user_id` scoping behavior and recall filtering
- Documents LLM requirement (`enrichment=True`)
- CHANGELOG entry for v0.8.0

**Estimate:** M

---

## Story Dependency Map

```
Phase 1 (Core Pipeline):
  S1 (types) → S2 (prompt) → S3 (extractor) → S4 (parsing)
                                    ↓
                              S5 (SDK method) → S6 (CLI)
                                    ↓
                              S7 (unit tests) + S8 (integration tests)

Phase 2 (MCP + Scoping):
  S5 → S9 (MCP tool)
  S10 (metadata) ← S3
  S11 (recall filtering) — independent of extraction

Phase 3 (REST API):
  S12 (migration) → S13 (POST endpoint) → S15 (background worker)
  S14 (GET endpoint) ← S12
  S16 (server tests) ← S13, S14, S15

Phase 4 (Hardening):
  S17 (chunking) — integrates into S3
  S18 (cost) — extends S6
  S19 (error recovery) — extends S3
  S20 (docs) — after all features complete
```
