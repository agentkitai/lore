# PRD: F6 — Metadata Enrichment (LLM-Powered)

**Feature:** Metadata Enrichment
**Version:** v0.6.0 ("Open Brain")
**Status:** Draft
**Author:** John (PM)
**Date:** 2026-03-06
**Dependencies:** None (uses existing `metadata` JSONB field)
**Dependents:** F1 (Knowledge Graph — consumes extracted entities), F2 (Fact Extraction — shares enrichment pipeline), F9 (Dialog Classification — shares enrichment pipeline)

---

## 1. Problem Statement

Lore stores memories as raw text with optional user-supplied tags. There is no automatic understanding of what a memory is about — no topics, no sentiment, no entity extraction, no categorization. This limits recall to pure semantic similarity and explicit tag matching.

Competitive platforms (Mem0, Zep, Cognee) automatically extract structured metadata from content, enabling richer filtering, better recall ranking, and knowledge graph construction. Lore needs the same capability — but as an optional, zero-cost-when-disabled feature that preserves the current "no API key required" philosophy.

## 2. Goals

1. **Automatic metadata extraction** — On `remember()`, optionally invoke an LLM to extract topics, sentiment, named entities, and categories from the memory content.
2. **Zero impact when disabled** — No LLM calls, no latency increase, no dependencies. Works exactly like today when enrichment is off (the default).
3. **Provider-agnostic** — Support OpenAI, Anthropic, and Google models via a lightweight LLM abstraction (litellm or similar).
4. **Filterable recall** — Enriched metadata powers new filter dimensions on `recall()` (e.g., `topic="deployment"`, `sentiment="positive"`).
5. **Batch backfill** — Enrich existing memories that were stored before enrichment was enabled.
6. **Foundation for intelligence pipeline** — F2 (Fact Extraction), F9 (Dialog Classification), and F1 (Knowledge Graph) will plug into the same enrichment pipeline architecture.

## 3. Non-Goals

- **Replacing embeddings** — Enrichment supplements semantic search, it does not replace vector similarity.
- **Real-time streaming enrichment** — Enrichment is synchronous on `remember()` or triggered manually. No background job queue (that's an F3/F7 concern).
- **Custom enrichment prompts** — V1 ships with a fixed extraction prompt. User-customizable prompts are a future consideration.
- **Enrichment for recall queries** — We enrich memories, not queries. Query understanding is a separate concern.
- **Fine-tuned models** — We use general-purpose LLMs with structured prompts. No model training.

## 4. Design

### 4.1 Enrichment Data Model

Enrichment results are stored in the existing `metadata` JSONB field under a dedicated `enrichment` key. No schema migration required.

```python
# Stored in memory.metadata["enrichment"]
{
    "topics": ["deployment", "kubernetes", "scaling"],
    "sentiment": {
        "label": "negative",        # positive | negative | neutral
        "score": -0.7               # -1.0 to 1.0
    },
    "entities": [
        {"name": "Kubernetes", "type": "tool"},
        {"name": "AWS", "type": "platform"},
        {"name": "Alice", "type": "person"},
        {"name": "auth-service", "type": "project"}
    ],
    "categories": ["infrastructure", "incident"],
    "enriched_at": "2026-03-06T14:30:00Z",
    "enrichment_model": "gpt-4o-mini"
}
```

**Field definitions:**

| Field | Type | Description |
|-------|------|-------------|
| `topics` | `List[str]` | 1-5 topic keywords extracted from content. Lowercase, deduplicated. |
| `sentiment.label` | `str` | One of: `positive`, `negative`, `neutral`. |
| `sentiment.score` | `float` | Continuous score from -1.0 (most negative) to 1.0 (most positive). |
| `entities` | `List[dict]` | Named entities with `name` (str) and `type` (str). Types: `person`, `tool`, `project`, `platform`, `organization`, `concept`, `language`, `framework`. |
| `categories` | `List[str]` | 1-3 high-level category labels. From a fixed set: `infrastructure`, `architecture`, `debugging`, `workflow`, `learning`, `preference`, `incident`, `convention`, `planning`, `documentation`, `testing`, `security`, `performance`, `other`. |
| `enriched_at` | `str` | ISO 8601 timestamp of when enrichment ran. |
| `enrichment_model` | `str` | Model identifier used for enrichment (for audit/reproducibility). |

### 4.2 Configuration

Enrichment is configured via the `Lore` constructor and/or environment variables.

```python
lore = Lore(
    enrichment=True,                          # Enable enrichment (default: False)
    enrichment_model="gpt-4o-mini",           # LLM model identifier (default: "gpt-4o-mini")
    enrichment_provider="openai",             # Provider: "openai", "anthropic", "google" (auto-detected from model name if omitted)
)
```

**Environment variables (override constructor):**

| Variable | Default | Description |
|----------|---------|-------------|
| `LORE_ENRICHMENT_ENABLED` | `false` | Enable/disable enrichment |
| `LORE_ENRICHMENT_MODEL` | `gpt-4o-mini` | Model to use for enrichment |
| `OPENAI_API_KEY` | — | Required if using OpenAI models |
| `ANTHROPIC_API_KEY` | — | Required if using Anthropic models |
| `GOOGLE_API_KEY` | — | Required if using Google models |

**Behavior when enabled but no API key:** Enrichment silently skips (logs a warning once), memory is saved without enrichment. No error, no failure.

### 4.3 LLM Provider Abstraction

A lightweight abstraction layer handles LLM calls across providers. Implementation options (in order of preference):

1. **litellm** — Already supports 100+ models with a unified interface. Add as optional dependency (`pip install lore-memory[enrichment]`).
2. **Direct SDK calls** — If litellm is too heavy, implement a minimal adapter for OpenAI, Anthropic, and Google SDKs.

```python
# src/lore/enrichment/llm.py

class LLMClient:
    """Thin wrapper for LLM calls. Uses litellm or direct SDKs."""

    def __init__(self, model: str, provider: Optional[str] = None):
        self.model = model
        self.provider = provider or self._detect_provider(model)

    def complete(self, prompt: str, response_format: type = None) -> str:
        """Send prompt to LLM, return response text."""
        # Implementation via litellm.completion() or direct SDK
        ...

    @staticmethod
    def _detect_provider(model: str) -> str:
        if model.startswith(("gpt-", "o1", "o3")):
            return "openai"
        elif model.startswith(("claude-",)):
            return "anthropic"
        elif model.startswith(("gemini-",)):
            return "google"
        raise ValueError(f"Cannot detect provider for model: {model}")
```

### 4.4 Enrichment Pipeline

```python
# src/lore/enrichment/pipeline.py

class EnrichmentPipeline:
    """Extracts structured metadata from memory content using an LLM."""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def enrich(self, content: str, context: Optional[str] = None) -> Dict[str, Any]:
        """Extract topics, sentiment, entities, categories from content.

        Returns enrichment dict ready to store in metadata["enrichment"].
        """
        prompt = self._build_prompt(content, context)
        response = self.llm.complete(prompt)
        enrichment = self._parse_response(response)
        enrichment["enriched_at"] = datetime.utcnow().isoformat() + "Z"
        enrichment["enrichment_model"] = self.llm.model
        return enrichment

    def _build_prompt(self, content: str, context: Optional[str]) -> str:
        """Build the extraction prompt."""
        # See Section 4.5 for prompt template
        ...

    def _parse_response(self, response: str) -> Dict[str, Any]:
        """Parse LLM JSON response into enrichment dict.

        Handles malformed JSON gracefully — returns partial results
        rather than failing entirely.
        """
        ...
```

### 4.5 Extraction Prompt

The prompt requests structured JSON output from the LLM:

```
Extract structured metadata from the following text. Return a JSON object with these fields:

- "topics": list of 1-5 topic keywords (lowercase). What is this text about?
- "sentiment": {"label": "positive"|"negative"|"neutral", "score": float from -1.0 to 1.0}
- "entities": list of {"name": string, "type": string} where type is one of: person, tool, project, platform, organization, concept, language, framework
- "categories": list of 1-3 categories from this set: infrastructure, architecture, debugging, workflow, learning, preference, incident, convention, planning, documentation, testing, security, performance, other

Text:
"""
{content}
"""

{context_section}

Return ONLY valid JSON. No explanation.
```

If `context` is provided, append:
```
Additional context:
"""
{context}
"""
```

### 4.6 Integration with remember()

```python
# In lore.py

def remember(self, content: str, *, metadata: Optional[Dict] = None, **kwargs) -> str:
    # ... existing logic (create memory, embed, redact) ...

    # Enrichment — after memory is constructed, before save
    if self._enrichment_pipeline:
        try:
            enrichment = self._enrichment_pipeline.enrich(content, context=kwargs.get("context"))
            if metadata is None:
                metadata = {}
            metadata["enrichment"] = enrichment
        except Exception as e:
            # Enrichment failure must never block memory storage
            logger.warning(f"Enrichment failed, saving without enrichment: {e}")

    memory = Memory(content=content, metadata=metadata, **kwargs)
    self._store.save(memory)
    return memory.id
```

**Critical rule:** Enrichment failure MUST NOT prevent memory storage. A `try/except` around the enrichment call ensures the memory is always saved. If enrichment fails (API error, rate limit, invalid response), the memory is saved without enrichment and a warning is logged.

### 4.7 Enriched Recall Filtering

Add optional filter parameters to `recall()`:

```python
def recall(
    self,
    query: str,
    *,
    topic: Optional[str] = None,          # NEW — filter by enrichment topic
    sentiment: Optional[str] = None,      # NEW — filter by sentiment label
    entity: Optional[str] = None,         # NEW — filter by entity name
    category: Optional[str] = None,       # NEW — filter by category
    # ... existing params (tags, type, tier, limit, etc.) ...
) -> List[RecallResult]:
```

**Filter implementation:** Post-retrieval filtering on the enrichment metadata. After vector similarity search returns candidates, filter out memories that don't match the enrichment criteria.

```python
def _matches_enrichment_filters(self, memory: Memory, topic, sentiment, entity, category) -> bool:
    enrichment = (memory.metadata or {}).get("enrichment", {})
    if not enrichment:
        return False  # Unenriched memories excluded when enrichment filters are active

    if topic and topic.lower() not in [t.lower() for t in enrichment.get("topics", [])]:
        return False
    if sentiment and enrichment.get("sentiment", {}).get("label") != sentiment:
        return False
    if entity and entity.lower() not in [e["name"].lower() for e in enrichment.get("entities", [])]:
        return False
    if category and category.lower() not in [c.lower() for c in enrichment.get("categories", [])]:
        return False
    return True
```

**Important:** When no enrichment filters are specified, unenriched memories are included normally (no regression).

### 4.8 Batch Enrichment

For backfilling existing memories that were stored without enrichment.

```python
# In lore.py

def enrich_memories(
    self,
    memory_ids: Optional[List[str]] = None,
    *,
    project: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Batch-enrich existing memories.

    Args:
        memory_ids: Specific memory IDs to enrich. If None, enrich all unenriched.
        project: Filter to a specific project (when memory_ids is None).
        force: Re-enrich memories that already have enrichment data.

    Returns:
        {"enriched": int, "skipped": int, "failed": int, "errors": List[str]}
    """
    if not self._enrichment_pipeline:
        raise RuntimeError("Enrichment is not enabled. Set enrichment=True in Lore config.")

    if memory_ids:
        memories = [self._store.get(mid) for mid in memory_ids]
        memories = [m for m in memories if m is not None]
    else:
        memories = self._store.list(project=project, limit=10000)

    results = {"enriched": 0, "skipped": 0, "failed": 0, "errors": []}

    for memory in memories:
        # Skip already-enriched unless force=True
        if not force and (memory.metadata or {}).get("enrichment"):
            results["skipped"] += 1
            continue

        try:
            enrichment = self._enrichment_pipeline.enrich(
                memory.content, context=memory.context
            )
            if memory.metadata is None:
                memory.metadata = {}
            memory.metadata["enrichment"] = enrichment
            self._store.update(memory)
            results["enriched"] += 1
        except Exception as e:
            results["failed"] += 1
            results["errors"].append(f"{memory.id}: {str(e)}")

    return results
```

### 4.9 MCP Tool: enrich

New MCP tool for manually triggering enrichment.

```python
@mcp.tool()
def enrich(
    memory_id: Optional[str] = None,
    all: bool = False,
    project: Optional[str] = None,
    force: bool = False,
) -> str:
    """Enrich memories with LLM-extracted metadata (topics, sentiment, entities, categories).

    Use memory_id to enrich a single memory, or set all=True to batch-enrich
    all unenriched memories. Requires enrichment to be enabled in Lore config.

    Args:
        memory_id: ID of a specific memory to enrich.
        all: If True, enrich all unenriched memories.
        project: Filter batch enrichment to a specific project.
        force: Re-enrich memories that already have enrichment data.
    """
    if memory_id:
        result = lore.enrich_memories(memory_ids=[memory_id], force=force)
    elif all:
        result = lore.enrich_memories(project=project, force=force)
    else:
        return "Provide memory_id or set all=True"

    return (
        f"Enrichment complete: {result['enriched']} enriched, "
        f"{result['skipped']} skipped, {result['failed']} failed."
    )
```

### 4.10 MCP Tool Updates: recall

Update `recall` tool to accept enrichment filter parameters:

```python
@mcp.tool()
def recall(
    query: str,
    # ... existing params ...
    topic: Optional[str] = None,       # NEW
    sentiment: Optional[str] = None,   # NEW
    entity: Optional[str] = None,      # NEW
    category: Optional[str] = None,    # NEW
) -> str:
    """Recall memories by semantic similarity.

    Optional enrichment filters (requires enrichment to be enabled):
    - topic: Filter by extracted topic (e.g., "deployment", "testing")
    - sentiment: Filter by sentiment label ("positive", "negative", "neutral")
    - entity: Filter by named entity (e.g., "Kubernetes", "Alice")
    - category: Filter by category (e.g., "infrastructure", "debugging")
    """
```

Update recall output to show enrichment data when present:

```
Memory [abc123] (score: 0.85, importance: 0.92)
Type: lesson | Tier: long | Tags: python, testing
Topics: deployment, kubernetes | Sentiment: negative (-0.7)
Entities: Kubernetes (tool), AWS (platform)
Content: The Kubernetes deployment failed because...
```

### 4.11 CLI Updates

**New `enrich` subcommand:**

```
lore enrich <memory-id>            # Enrich a single memory
lore enrich --all                  # Batch-enrich all unenriched memories
lore enrich --all --project myproj # Batch-enrich for a specific project
lore enrich --all --force          # Re-enrich everything (overwrite existing)
```

**Updated `recall` subcommand:**

```
lore recall "deployment issues" --topic deployment --sentiment negative
lore recall "auth problems" --entity auth-service --category security
```

Add `--topic`, `--sentiment`, `--entity`, `--category` arguments.

**Updated `memories` (list) output:**

When enrichment data is present, show topics in the table output:

```
ID          Type      Tier   Topics                    Content
abc123      lesson    long   deployment, kubernetes    The Kubernetes deployment...
def456      code      short  —                         Quick fix for parse...
```

### 4.12 Server API Updates (Postgres Backend)

**POST /api/v1/memories** — No change needed. `metadata` field already accepts arbitrary JSONB, so `metadata.enrichment` is stored automatically.

**POST /api/v1/memories/search** — Accept optional enrichment filter parameters:
```json
{
    "query": "deployment",
    "topic": "kubernetes",
    "sentiment": "negative"
}
```

Server-side filtering via JSONB operators:
```sql
WHERE metadata->'enrichment'->'topics' ? 'kubernetes'
  AND metadata->'enrichment'->'sentiment'->>'label' = 'negative'
```

**POST /api/v1/enrich** — New endpoint for batch enrichment (mirrors `enrich_memories()` method). Only available when server has enrichment configured.

## 5. Module Structure

```
src/lore/enrichment/
    __init__.py          # Public API: EnrichmentPipeline, LLMClient
    llm.py               # LLM provider abstraction (LLMClient)
    pipeline.py           # EnrichmentPipeline class
    prompts.py            # Extraction prompt templates
```

## 6. Dependencies

### 6.1 Required (when enrichment is enabled)

| Package | Purpose | Install |
|---------|---------|---------|
| `litellm` | Unified LLM API (OpenAI/Anthropic/Google) | `pip install lore-memory[enrichment]` |

### 6.2 Optional dependency pattern

```toml
# pyproject.toml
[project.optional-dependencies]
enrichment = ["litellm>=1.0"]
```

If `enrichment=True` but litellm is not installed, raise a clear error:
```
ImportError: Enrichment requires the 'litellm' package.
Install it with: pip install lore-memory[enrichment]
```

## 7. Error Handling

| Scenario | Behavior |
|----------|----------|
| Enrichment enabled, no API key | Warning logged once, enrichment skipped, memory saved normally |
| LLM API call fails (network, rate limit) | Warning logged, enrichment skipped, memory saved normally |
| LLM returns malformed JSON | Best-effort partial parse; missing fields set to empty defaults |
| LLM returns unexpected field values | Validate and clamp (e.g., sentiment score clamped to [-1, 1]) |
| Batch enrichment: some memories fail | Continue processing remaining; return summary with error details |
| litellm not installed | `ImportError` with install instructions on `Lore(enrichment=True)` |

**Principle:** Enrichment is always best-effort. It must never block, crash, or degrade the core remember/recall flow.

## 8. Performance Considerations

| Concern | Mitigation |
|---------|-----------|
| LLM call adds latency to `remember()` | Use cheap, fast models (gpt-4o-mini ~200ms). Enrichment is optional. |
| Batch enrichment may hit rate limits | No parallelism in V1 — sequential processing with error handling. Rate limiting is the user's responsibility via their API key limits. |
| JSONB metadata size increase | Enrichment adds ~500 bytes per memory. Negligible. |
| Post-retrieval filtering reduces recall results | Fetch 3x the requested limit before filtering, then trim to limit. |

## 9. Implementation Plan

### 9.1 Task Breakdown

1. **src/lore/enrichment/llm.py** — LLM client abstraction with provider detection.
2. **src/lore/enrichment/prompts.py** — Extraction prompt template.
3. **src/lore/enrichment/pipeline.py** — `EnrichmentPipeline` with `enrich()` method, JSON parsing, validation.
4. **src/lore/lore.py** — Constructor changes (enrichment config), `remember()` integration, `enrich_memories()` method, recall enrichment filtering.
5. **src/lore/mcp/server.py** — New `enrich` tool, recall filter params, enrichment display in output.
6. **src/lore/cli.py** — `enrich` subcommand, recall filter args, enrichment display in list output.
7. **src/lore/server/** — Search endpoint enrichment filters, new `/enrich` endpoint.
8. **tests/test_enrichment.py** — Unit tests for pipeline, filtering, batch enrichment, error handling.
9. **pyproject.toml** — Add `[enrichment]` optional dependency.

### 9.2 Testing Strategy

- **Unit tests:** Mock LLM calls. Test prompt construction, response parsing (valid JSON, malformed JSON, partial responses), validation/clamping.
- **Integration tests:** Test `remember()` with enrichment enabled (mocked LLM), verify metadata is stored. Test `recall()` with enrichment filters.
- **Error path tests:** API failures, missing API keys, malformed responses, litellm not installed.
- **Batch tests:** `enrich_memories()` with mix of enriched/unenriched, force mode, partial failures.

## 10. Acceptance Criteria

### Must Have (P0)

- [ ] `Lore(enrichment=True, enrichment_model="gpt-4o-mini")` enables enrichment pipeline.
- [ ] `remember()` with enrichment enabled calls LLM and stores result in `metadata.enrichment`.
- [ ] `remember()` with enrichment disabled (default) makes zero LLM calls and behaves identically to v0.5.x.
- [ ] Enrichment failure does not prevent memory storage — memory is saved without enrichment.
- [ ] Enrichment extracts: topics (list), sentiment (label + score), entities (name + type), categories (list).
- [ ] `recall()` accepts `topic`, `sentiment`, `entity`, `category` filter params.
- [ ] Recall with enrichment filters returns only matching memories.
- [ ] Recall without enrichment filters includes unenriched memories normally (no regression).
- [ ] `enrich_memories()` batch-enriches existing memories; skips already-enriched unless `force=True`.
- [ ] MCP `enrich` tool triggers enrichment on specific memory or all memories.
- [ ] CLI `lore enrich <id>` and `lore enrich --all` work correctly.
- [ ] CLI `lore recall` accepts `--topic`, `--sentiment`, `--entity`, `--category`.
- [ ] LLM provider abstraction supports OpenAI, Anthropic, and Google models.
- [ ] Missing API key with enrichment enabled logs warning and skips (no crash).
- [ ] All existing tests pass unchanged (zero regression).

### Should Have (P1)

- [ ] MCP `recall` output displays enrichment data (topics, sentiment, entities) when present.
- [ ] CLI `lore memories` output shows topics column when enrichment data exists.
- [ ] Batch enrichment returns summary: `{enriched, skipped, failed, errors}`.
- [ ] Malformed LLM responses are partially parsed — extract whatever fields are valid.
- [ ] `enriched_at` and `enrichment_model` are recorded for audit.

### Could Have (P2)

- [ ] Server-side JSONB filtering for enrichment fields on search endpoint.
- [ ] Server `/enrich` endpoint for batch enrichment via API.
- [ ] Over-fetch (3x limit) before enrichment filtering to maintain result count.

## 11. Interaction with Existing Systems

### Metadata Field
Enrichment uses the existing `metadata` JSONB field. No schema migration required. The `enrichment` key is namespaced to avoid conflicts with user-supplied metadata.

### Importance Scoring (F5)
Enrichment data does not directly affect importance scoring in V1. Future consideration: sentiment or entity count could feed into importance calculation.

### Memory Tiers (F4)
Enrichment applies to all tiers equally. Tier does not affect enrichment behavior.

### Redaction Pipeline
Enrichment runs AFTER PII redaction. The LLM sees redacted content only, preserving privacy guarantees.

### Tags
Enrichment topics are separate from user-supplied tags. They live in `metadata.enrichment.topics`, not in `memory.tags`. Users can still set tags manually; enrichment topics supplement but do not replace them.

## 12. Future Considerations (Out of Scope)

- **Enrichment pipeline extensibility** — F2 (Fact Extraction) and F9 (Dialog Classification) will add steps to the pipeline. The architecture should make this easy, but the plugin system itself is out of scope for F6.
- **Custom extraction prompts** — Let users define their own enrichment schema/prompt.
- **Async enrichment** — Background job queue for enrichment (decouple from `remember()` latency).
- **Enrichment caching** — Cache enrichment results for identical content to reduce LLM calls.
- **Enrichment-boosted recall scoring** — Use topic/entity overlap between query and memory enrichment to boost relevance scores.
- **Embedding enriched text** — Concatenate enrichment metadata into the text before embedding for richer vector representations.

## 13. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| LLM API costs for large batch enrichment | Medium | Default to cheapest model (gpt-4o-mini ~$0.15/1M tokens). Batch enrichment is explicit opt-in. |
| LLM latency on `remember()` | Medium | Cheap models are fast (~200ms). Enrichment is optional and off by default. |
| LLM provider API changes | Low | litellm abstracts this. Pin litellm version for stability. |
| Enrichment quality varies by model | Low | Default to gpt-4o-mini which is good at structured extraction. Users can choose better models. |
| JSON parsing failures from LLMs | Medium | Robust parser with fallbacks. Request JSON mode where supported. Partial results better than no results. |
| Privacy: sending content to external LLM | Medium | Runs AFTER redaction pipeline. Document clearly that enrichment sends content to external APIs. Users opt in explicitly. |

## 14. Success Metrics

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Enrichment extraction accuracy | > 80% of topics/entities match human judgment | Manual review of 50 enriched memories |
| Enrichment latency overhead on remember() | < 500ms p95 with gpt-4o-mini | Benchmark: time remember() with and without enrichment |
| Enrichment failure rate | < 2% of calls | Monitor errors in batch enrichment results |
| Zero regression when disabled | All 590+ existing tests pass | pytest full suite with enrichment=False (default) |
| Recall filter precision | 100% — filtered results always match criteria | Test with known enrichment data |
| Batch enrichment throughput | > 100 memories/minute with gpt-4o-mini | Benchmark enrich_memories() on 500 memories |
