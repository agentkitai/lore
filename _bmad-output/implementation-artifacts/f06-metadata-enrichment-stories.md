# User Stories: F6 — Metadata Enrichment (LLM-Powered)

**Feature:** F6 Metadata Enrichment
**Version:** v0.6.0 ("Open Brain")
**Created:** 2026-03-06
**Author:** SM Agent

---

## Story 1: LLM Provider Abstraction

**As a** developer integrating LLM-powered enrichment,
**I want** a provider-agnostic LLM client that wraps litellm,
**so that** enrichment works with OpenAI, Anthropic, and Google models without provider-specific code.

**Size:** S

**Dependencies:** None

**Files:**
- `src/lore/enrichment/__init__.py` (new)
- `src/lore/enrichment/llm.py` (new)
- `pyproject.toml` (add `[enrichment]` optional dependency: `litellm>=1.0`)

**Acceptance Criteria:**

```gherkin
Given litellm is installed
When I create LLMClient(model="gpt-4o-mini")
Then provider is auto-detected as "openai"

Given litellm is installed
When I create LLMClient(model="claude-3-haiku")
Then provider is auto-detected as "anthropic"

Given litellm is installed
When I create LLMClient(model="gemini-pro")
Then provider is auto-detected as "google"

Given litellm is installed
When I create LLMClient(model="unknown-model")
Then provider falls back to "openai"

Given litellm is NOT installed
When I create LLMClient(model="gpt-4o-mini")
Then an ImportError is raised with message containing "pip install lore-memory[enrichment]"

Given an LLMClient with provider "openai"
And OPENAI_API_KEY is not set
When I call check_api_key()
Then it returns False and logs a warning

Given an LLMClient with provider "openai"
And OPENAI_API_KEY is not set
When I call check_api_key() twice
Then the warning is logged only once (warn-once behavior)

Given an LLMClient with provider "openai"
And OPENAI_API_KEY is set
When I call check_api_key()
Then it returns True

Given an LLMClient with a valid API key
When I call complete(prompt) (mocked litellm)
Then it calls litellm.completion with model, messages, and temperature=0.0
And returns the response text
```

**Implementation Notes:**
- Lazy import: `import litellm` only inside `__init__` and `complete()`, never at module level
- `temperature=0.0` for deterministic structured extraction
- `response_format` parameter passed through to litellm when provided (for JSON mode)
- `_detect_provider()` is a static method for testability

**Tests:**
- `tests/test_enrichment_llm.py`: 8 tests covering provider detection, API key checks, import error, and completion call

---

## Story 2: Enrichment Prompt Templates

**As a** developer building the enrichment pipeline,
**I want** a prompt template that instructs LLMs to extract topics, sentiment, entities, and categories as structured JSON,
**so that** the pipeline gets consistent, parseable output from any supported model.

**Size:** S

**Dependencies:** None (can be implemented in parallel with Story 1)

**Files:**
- `src/lore/enrichment/prompts.py` (new)

**Acceptance Criteria:**

```gherkin
Given content text "The Kubernetes deployment failed on AWS"
When I call build_extraction_prompt(content)
Then the prompt includes the content wrapped in triple-quotes
And the prompt requests JSON with fields: topics, sentiment, entities, categories
And the prompt lists valid category values
And the prompt lists valid entity types
And no context section is included

Given content text and context "Production incident on 2026-03-01"
When I call build_extraction_prompt(content, context=context)
Then the prompt includes the content section
And the prompt includes an "Additional context" section with the context text

Given any content
When I call build_extraction_prompt(content)
Then the prompt ends with "Return ONLY valid JSON. No explanation."
```

**Implementation Notes:**
- `_EXTRACTION_TEMPLATE` string with `{content}` and `{context_section}` placeholders
- `_CONTEXT_SECTION` appended only when context is provided
- Categories and entity types hardcoded in prompt to constrain LLM output

**Tests:**
- `tests/test_enrichment.py`: 3 tests for prompt construction (with context, without context, format verification)

---

## Story 3: EnrichmentPipeline Class

**As a** developer building enrichment,
**I want** an EnrichmentPipeline that orchestrates LLM calls, parses JSON responses, validates/sanitizes fields, and returns a storage-ready enrichment dict,
**so that** the enrichment output is always well-structured regardless of LLM response quality.

**Size:** M

**Dependencies:** Story 1 (LLMClient), Story 2 (prompts)

**Files:**
- `src/lore/enrichment/pipeline.py` (new)
- `src/lore/enrichment/__init__.py` (update exports)

**Acceptance Criteria:**

```gherkin
Given a mocked LLM returning valid JSON with topics, sentiment, entities, categories
When I call pipeline.enrich(content)
Then it returns a dict with all four fields correctly parsed
And "enriched_at" is set to current UTC ISO timestamp
And "enrichment_model" matches the LLM client's model

Given a mocked LLM returning JSON wrapped in ```json ... ``` code fences
When I call pipeline.enrich(content)
Then the fences are stripped and the JSON is parsed successfully

Given a mocked LLM returning completely invalid JSON
When I call pipeline.enrich(content)
Then it returns defaults: empty topics, neutral sentiment (score 0.0), empty entities, empty categories
And a warning is logged

Given a mocked LLM returning JSON with 8 topics
When parsing completes
Then topics are truncated to 5

Given a mocked LLM returning mixed-case topics like ["Kubernetes", "AWS"]
When parsing completes
Then all topics are lowercased: ["kubernetes", "aws"]

Given a mocked LLM returning sentiment score of 2.5
When parsing completes
Then the score is clamped to 1.0

Given a mocked LLM returning sentiment score of -3.0
When parsing completes
Then the score is clamped to -1.0

Given a mocked LLM returning sentiment label "amazing"
When parsing completes
Then the label defaults to "neutral"

Given a mocked LLM returning an entity with type "database"
When parsing completes
Then the entity type defaults to "concept"

Given a mocked LLM returning categories ["infrastructure", "banana", "debugging", "testing", "security"]
When parsing completes
Then only valid categories are kept and truncated to 3: ["infrastructure", "debugging", "testing"]

Given a mocked LLM and no API key configured
When I call pipeline.enrich(content)
Then a RuntimeError is raised (caller handles)
```

**Implementation Notes:**
- `EnrichmentResult` dataclass with `to_dict()` for serialization
- `VALID_CATEGORIES`, `VALID_ENTITY_TYPES`, `VALID_SENTIMENTS` as frozensets for validation
- Best-effort parsing: partial results preferred over total failure
- Pipeline raises on API key missing or LLM failure; caller (`remember()`) catches

**Tests:**
- `tests/test_enrichment.py`: 13 unit tests covering valid parse, code fences, malformed JSON, partial JSON, topic truncation/lowercase, sentiment clamping/invalid label, entity type fallback, category validation/truncation

---

## Story 4: Integration into remember()

**As a** user storing memories,
**I want** enrichment to run automatically on `remember()` when enabled, extracting metadata without blocking storage on failure,
**so that** my memories are enriched transparently while maintaining the guarantee that storage never fails due to enrichment.

**Size:** M

**Dependencies:** Story 3 (EnrichmentPipeline)

**Files:**
- `src/lore/lore.py` (modify: constructor, `remember()`)

**Acceptance Criteria:**

```gherkin
Given Lore configured with enrichment=True and a mocked LLM
When I call remember("The K8s deployment failed on AWS")
Then the memory is saved with metadata["enrichment"] containing topics, sentiment, entities, categories
And enrichment runs AFTER PII redaction (LLM sees redacted content only)

Given Lore configured with enrichment=True and a mocked LLM that raises an exception
When I call remember("some content")
Then the memory is saved WITHOUT enrichment metadata
And a warning is logged: "Enrichment failed, saving without"

Given Lore configured with enrichment=False (default)
When I call remember("some content")
Then no LLM call is made
And no enrichment key exists in metadata
And behavior is identical to pre-F6

Given Lore configured with enrichment=True but no API key
When I call remember("some content")
Then enrichment is skipped (no crash)
And the memory is saved without enrichment
And a warning is logged once

Given LORE_ENRICHMENT_ENABLED=true in environment
When I create Lore() without passing enrichment=True
Then enrichment is enabled via environment variable

Given LORE_ENRICHMENT_MODEL=claude-3-haiku in environment
When enrichment runs
Then the specified model is used instead of the default gpt-4o-mini

Given Lore with enrichment enabled and user-supplied metadata={"custom": "value"}
When I call remember(content, metadata={"custom": "value"})
Then metadata contains both "custom" and "enrichment" keys
```

**Implementation Notes:**
- Enrichment import is lazy (inside `if enrichment:` block in constructor)
- `try/except Exception` around enrichment call — must never block save
- Enrichment ordering: validation -> redaction -> embedding -> **enrichment** -> save
- Environment variables override constructor args (`LORE_ENRICHMENT_ENABLED`, `LORE_ENRICHMENT_MODEL`)

**Tests:**
- `tests/test_enrichment_integration.py`: 7 tests covering success, failure, disabled, no API key, env vars, existing metadata merge

---

## Story 5: Recall Enrichment Filtering

**As a** user recalling memories,
**I want** to filter results by enrichment metadata (topic, sentiment, entity, category),
**so that** I can narrow recall to memories about specific subjects, with specific sentiments, or mentioning specific entities.

**Size:** M

**Dependencies:** Story 4 (remember integration, so enriched memories exist)

**Files:**
- `src/lore/lore.py` (modify: `recall()`, `_recall_local()`, new `_matches_enrichment_filters()`)

**Acceptance Criteria:**

```gherkin
Given memories enriched with topics ["kubernetes", "deployment"]
When I call recall(query, topic="kubernetes")
Then only memories with "kubernetes" in their topics are returned

Given memories with sentiment label "negative"
When I call recall(query, sentiment="negative")
Then only memories with negative sentiment are returned

Given memories with entity {"name": "AWS", "type": "platform"}
When I call recall(query, entity="AWS")
Then only memories mentioning entity "AWS" are returned

Given memories with categories ["infrastructure"]
When I call recall(query, category="infrastructure")
Then only memories categorized as "infrastructure" are returned

Given mixed enriched and unenriched memories
When I call recall(query, topic="kubernetes")
Then unenriched memories are excluded from results

Given mixed enriched and unenriched memories
When I call recall(query) with NO enrichment filters
Then all memories (enriched and unenriched) are included as before (zero regression)

Given recall with enrichment filters and limit=5
When filtering reduces the candidate pool
Then over-fetch (3x limit) is applied before filtering to maintain result count

Given topic filter "Kubernetes" (uppercase)
When matching against enrichment topics ["kubernetes"]
Then the match is case-insensitive and succeeds

Given recall with topic="deployment" AND sentiment="negative"
When filtering
Then only memories matching BOTH criteria are returned
```

**Implementation Notes:**
- Post-retrieval filtering: cosine scoring first, then enrichment filter
- Over-fetch 3x when enrichment filters active, then filter, then trim to limit
- `_matches_enrichment_filters()` helper: returns False for unenriched memories when any filter is active
- All string comparisons are case-insensitive

**Tests:**
- `tests/test_enrichment_integration.py`: 9 tests covering each filter type, combined filters, unenriched exclusion, no-filter regression, case insensitivity, over-fetch

---

## Story 6: Batch Enrichment

**As a** user with existing unenriched memories,
**I want** to batch-enrich them via an `enrich_memories()` method,
**so that** memories stored before enrichment was enabled can benefit from metadata extraction.

**Size:** M

**Dependencies:** Story 3 (EnrichmentPipeline)

**Files:**
- `src/lore/lore.py` (modify: add `enrich_memories()` method)

**Acceptance Criteria:**

```gherkin
Given 5 unenriched memories and a mocked LLM
When I call enrich_memories()
Then all 5 memories are enriched and updated in the store
And the result is {"enriched": 5, "skipped": 0, "failed": 0, "errors": []}

Given 3 enriched and 2 unenriched memories
When I call enrich_memories(force=False)
Then only the 2 unenriched are enriched
And the result shows skipped: 3, enriched: 2

Given 3 enriched memories
When I call enrich_memories(force=True)
Then all 3 are re-enriched with fresh metadata
And the result shows enriched: 3, skipped: 0

Given specific memory IDs ["id1", "id3"]
When I call enrich_memories(memory_ids=["id1", "id3"])
Then only those 2 memories are enriched

Given 5 memories and LLM fails on memory 3
When I call enrich_memories()
Then memories 1, 2, 4, 5 are enriched
And memory 3 is recorded as failed with its error message
And result is {"enriched": 4, "skipped": 0, "failed": 1, "errors": ["id3: ..."]}

Given enrichment is NOT enabled (enrichment=False)
When I call enrich_memories()
Then a RuntimeError is raised with message "Enrichment not enabled"

Given memories in project "myproj" and other projects
When I call enrich_memories(project="myproj")
Then only memories in "myproj" are enriched
```

**Implementation Notes:**
- Sequential processing (no parallelism in V1)
- `limit=10000` on store.list() to prevent unbounded queries
- Per-memory try/except: errors collected, batch continues
- Uses `store.update()` to persist enrichment metadata

**Tests:**
- `tests/test_enrichment_integration.py`: 7 tests covering all-unenriched, skip-enriched, force, by-IDs, partial failure, not-enabled error, project filter

---

## Story 7: MCP Tool and CLI Commands

**As a** user interacting with Lore via MCP or CLI,
**I want** an `enrich` command/tool and enrichment filter params on `recall`,
**so that** I can trigger enrichment and filter by enrichment metadata from any interface.

**Size:** L

**Dependencies:** Story 5 (recall filtering), Story 6 (batch enrichment)

**Files:**
- `src/lore/mcp/server.py` (modify: new `enrich` tool, `recall` filter params, output formatting, `_get_lore()` config)
- `src/lore/cli.py` (modify: new `enrich` subcommand, `recall` filter flags, output formatting)

**Acceptance Criteria:**

```gherkin
# MCP — enrich tool
Given enrichment is enabled on the MCP server
When I call the enrich tool with memory_id="abc123"
Then that single memory is enriched and a success message is returned

Given enrichment is enabled
When I call enrich(all=True)
Then all unenriched memories are batch-enriched
And the result message shows enriched/skipped/failed counts

Given enrichment is enabled
When I call enrich(all=True, project="myproj", force=True)
Then only memories in "myproj" are re-enriched

Given enrichment is NOT enabled
When I call the enrich tool
Then the error message "Enrichment not enabled" is returned (no crash)

Given neither memory_id nor all=True is provided
When I call the enrich tool
Then it returns "Provide memory_id or set all=True."

# MCP — recall filters
Given enriched memories exist
When I call recall(query="deploy", topic="kubernetes")
Then only memories matching the topic filter are returned

Given enriched memories exist
When I call recall with topic, sentiment, entity, or category params
Then those params are passed through to lore.recall()

# MCP — recall output
Given a recalled memory has enrichment data
When formatting recall output
Then topics, sentiment (label + score), entities, and categories are displayed

# MCP — _get_lore() config
Given LORE_ENRICHMENT_ENABLED=true in environment
When _get_lore() initializes
Then Lore is created with enrichment=True

# CLI — enrich subcommand
Given CLI
When I run "lore enrich abc123"
Then memory abc123 is enriched

Given CLI
When I run "lore enrich --all --project myproj --force"
Then all memories in myproj are re-enriched

Given CLI
When I run "lore enrich" with no arguments
Then an error message is printed and exit code is 1

Given CLI enrich with --model flag
When I run "lore enrich --all --model claude-3-haiku"
Then enrichment uses the specified model

# CLI — recall filters
Given CLI
When I run "lore recall 'deploy issues' --topic deployment --sentiment negative"
Then recall results are filtered by topic and sentiment

Given CLI recall results with enrichment data
When displaying results
Then topics are shown in the output

# CLI — memories list
Given memories with enrichment data
When I run "lore memories" (list)
Then topics column shows extracted topics for enriched memories and "-" for unenriched
```

**Implementation Notes:**
- MCP `enrich` tool catches RuntimeError and general exceptions, returns error strings (never crashes)
- CLI `enrich` always creates Lore with `enrichment=True` (it's explicitly requesting enrichment)
- CLI `--model` flag allows overriding the enrichment model
- Recall output formatting: only show enrichment lines when data is present
- `_get_lore()` reads `LORE_ENRICHMENT_ENABLED` and `LORE_ENRICHMENT_MODEL` from env

**Tests:**
- MCP tool tests (if existing test patterns exist for MCP tools)
- CLI integration tests for `enrich` subcommand and recall filter flags

---

## Story 8: Comprehensive Test Suite

**As a** developer maintaining Lore,
**I want** thorough unit and integration tests for all enrichment functionality with mocked LLM calls,
**so that** enrichment is reliable, edge cases are covered, and regressions are caught.

**Size:** M

**Dependencies:** Stories 1-7 (all implementation stories)

**Files:**
- `tests/test_enrichment.py` (new — unit tests for pipeline, prompts, validation)
- `tests/test_enrichment_llm.py` (new — unit tests for LLMClient)
- `tests/test_enrichment_integration.py` (new — integration tests for remember/recall/batch)

**Acceptance Criteria:**

```gherkin
Given the test suite
When I run pytest
Then all enrichment tests pass with mocked LLM calls (zero real API calls)

Given the existing 590+ test suite
When I run pytest with enrichment=False (default)
Then all existing tests pass unchanged (zero regression)

Given unit tests for pipeline parsing
When LLM returns valid JSON, code-fenced JSON, malformed JSON, or partial JSON
Then each case is handled correctly per Story 3 criteria

Given unit tests for validation
When topics exceed 5, sentiment score exceeds bounds, entity types are invalid, or categories are not in the fixed set
Then validation rules clamp/default/filter per Story 3 criteria

Given integration tests for remember()
When enrichment succeeds, fails, is disabled, or has no API key
Then each scenario behaves per Story 4 criteria

Given integration tests for recall()
When enrichment filters are applied or omitted
Then filtering behavior matches Story 5 criteria

Given integration tests for enrich_memories()
When batch enrichment runs with various scenarios
Then behavior matches Story 6 criteria

Given all test files
When reviewing mock strategy
Then all LLM calls use unittest.mock.patch on litellm.completion
And mock responses follow the MOCK_ENRICHMENT_RESPONSE pattern from the architecture doc
```

**Implementation Notes:**
- Mock strategy: `@patch("lore.enrichment.llm.litellm")` with MagicMock response objects
- Test files organized by scope: unit (pipeline/llm) vs integration (remember/recall/batch)
- Tests written alongside each story — this story tracks final coverage verification and any gap-filling
- Target: 40+ enrichment-specific tests across all test files

**Tests:** This IS the test story. Deliverable is the complete test suite.

---

## Dependency Graph

```
Story 1 (LLM Client)  ──┐
                         ├──> Story 3 (Pipeline) ──> Story 4 (remember) ──> Story 5 (recall filters)
Story 2 (Prompts)     ──┘                        └──> Story 6 (batch)   ──┘
                                                                            └──> Story 7 (MCP + CLI)
                                                                                      │
Story 8 (Tests) ── runs alongside all stories, finalized after Story 7 ───────────────┘
```

**Parallelizable:** Stories 1 & 2 (no dependencies). Stories 5 & 6 (both depend on 3/4 but not each other).

---

## Estimates Summary

| Story | Size | Description |
|-------|------|-------------|
| S1 | S | LLM provider abstraction (LLMClient + pyproject.toml) |
| S2 | S | Enrichment prompt templates |
| S3 | M | EnrichmentPipeline (orchestrate, parse, validate) |
| S4 | M | Integration into remember() |
| S5 | M | Recall enrichment filtering |
| S6 | M | Batch enrichment (enrich_memories) |
| S7 | L | MCP tool + CLI commands |
| S8 | M | Comprehensive test suite |

**Total:** 2S + 5M + 1L
