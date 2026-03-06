# Architecture: F6 — Metadata Enrichment (LLM-Powered)

**Version:** 1.0
**Author:** Architect Agent
**Date:** 2026-03-06
**PRD:** `_bmad-output/planning-artifacts/f06-metadata-enrichment-prd.md`
**Depends on:** None (uses existing `metadata` JSONB field)
**Dependents:** F1 (Knowledge Graph), F2 (Fact Extraction), F9 (Dialog Classification) — all share the enrichment pipeline

---

## 1. Overview

This document specifies how to implement LLM-powered metadata enrichment for Lore's `remember()` flow. When enabled, an LLM extracts topics, sentiment, named entities, and categories from memory content and stores them in `metadata.enrichment`. The feature is fully optional — zero cost, zero dependencies, zero latency when disabled.

### Architecture Principles

1. **Optional by default** — Enrichment is off unless explicitly enabled. No LLM dependency in the core SDK.
2. **Fail-safe** — Enrichment failure never blocks memory storage. Always best-effort.
3. **Provider-agnostic** — Support OpenAI, Anthropic, and Google via litellm (optional dependency).
4. **Pipeline-ready** — Architecture supports future enrichment steps (F2 Fact Extraction, F9 Dialog Classification) plugging into the same pipeline.
5. **No schema migration** — All enrichment data lives in the existing `metadata` JSONB field under the `enrichment` key.

---

## 2. Module Structure

```
src/lore/enrichment/
    __init__.py          # Public API: EnrichmentPipeline, LLMClient, EnrichmentResult
    llm.py               # LLM provider abstraction (LLMClient)
    pipeline.py          # EnrichmentPipeline class
    prompts.py           # Extraction prompt template
```

### 2.1 Dependency Graph

```
pyproject.toml
  └── [enrichment] optional dep → litellm>=1.0

enrichment/__init__.py
  ├── exports: EnrichmentPipeline, LLMClient, EnrichmentResult
  └── imports from: pipeline.py, llm.py

enrichment/llm.py
  ├── LLMClient
  └── imports: litellm (lazy, with ImportError guard)

enrichment/pipeline.py
  ├── EnrichmentPipeline
  └── imports: llm.py, prompts.py

enrichment/prompts.py
  └── EXTRACTION_PROMPT (string template, no imports)

lore.py
  ├── __init__: conditionally creates EnrichmentPipeline
  ├── remember(): calls pipeline.enrich() in try/except
  ├── recall(): applies enrichment filters post-retrieval
  └── enrich_memories(): batch enrichment method

mcp/server.py
  ├── recall: new filter params (topic, sentiment, entity, category)
  └── enrich: new MCP tool

cli.py
  ├── recall: new filter flags
  └── enrich: new subcommand
```

---

## 3. LLM Provider Abstraction

### 3.1 `src/lore/enrichment/llm.py`

```python
"""Lightweight LLM client abstraction using litellm."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_LITELLM_IMPORT_ERROR = (
    "Enrichment requires the 'litellm' package. "
    "Install with: pip install lore-memory[enrichment]"
)


class LLMClient:
    """Thin wrapper for LLM completion calls.

    Uses litellm for provider-agnostic access to OpenAI, Anthropic,
    and Google models.
    """

    def __init__(self, model: str, provider: Optional[str] = None) -> None:
        try:
            import litellm  # noqa: F401
        except ImportError:
            raise ImportError(_LITELLM_IMPORT_ERROR)

        self.model = model
        self.provider = provider or self._detect_provider(model)
        self._warned_no_key = False

    def complete(self, prompt: str, response_format: Optional[Dict[str, Any]] = None) -> str:
        """Send prompt to LLM, return response text.

        Raises on network/API errors — caller must handle.
        """
        import litellm

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
        }
        if response_format:
            kwargs["response_format"] = response_format

        response = litellm.completion(**kwargs)
        return response.choices[0].message.content

    def check_api_key(self) -> bool:
        """Check if the required API key is available.

        Returns True if key is present, False otherwise.
        Logs a warning once if key is missing.
        """
        import os

        key_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google": "GOOGLE_API_KEY",
        }
        env_var = key_map.get(self.provider)
        if env_var and not os.environ.get(env_var):
            if not self._warned_no_key:
                logger.warning(
                    "Enrichment skipped: %s not set for provider '%s'",
                    env_var, self.provider,
                )
                self._warned_no_key = True
            return False
        return True

    @staticmethod
    def _detect_provider(model: str) -> str:
        """Auto-detect provider from model name."""
        if model.startswith(("gpt-", "o1", "o3", "o4")):
            return "openai"
        if model.startswith(("claude-",)):
            return "anthropic"
        if model.startswith(("gemini-",)):
            return "google"
        # Fallback: let litellm figure it out
        return "openai"
```

### 3.2 Design Decisions

| Decision | Rationale |
|----------|-----------|
| litellm over direct SDKs | Supports 100+ models with one dependency. Avoids maintaining 3 separate SDK adapters. |
| Lazy import of litellm | `import litellm` only in `__init__` and `complete()`, not at module level. Prevents ImportError for users who don't use enrichment. |
| `_detect_provider()` as static method | Pure function, testable without instantiation. Falls back to "openai" for unknown prefixes (litellm can usually resolve). |
| `check_api_key()` with warn-once | Prevents log spam on repeated `remember()` calls when key is missing. |
| `temperature=0.0` | Structured extraction needs deterministic output. |
| `response_format` parameter | Enables JSON mode on models that support it (OpenAI `response_format={"type": "json_object"}`). Not all providers support it, so it's optional. |

---

## 4. Enrichment Pipeline

### 4.1 `src/lore/enrichment/pipeline.py`

```python
"""Enrichment pipeline for extracting structured metadata from memory content."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from lore.enrichment.llm import LLMClient
from lore.enrichment.prompts import build_extraction_prompt

logger = logging.getLogger(__name__)

# Fixed category set for validation
VALID_CATEGORIES = frozenset({
    "infrastructure", "architecture", "debugging", "workflow",
    "learning", "preference", "incident", "convention",
    "planning", "documentation", "testing", "security",
    "performance", "other",
})

VALID_ENTITY_TYPES = frozenset({
    "person", "tool", "project", "platform",
    "organization", "concept", "language", "framework",
})

VALID_SENTIMENTS = frozenset({"positive", "negative", "neutral"})


class EnrichmentResult:
    """Parsed, validated enrichment data ready for storage."""

    def __init__(
        self,
        topics: List[str],
        sentiment: Dict[str, Any],
        entities: List[Dict[str, str]],
        categories: List[str],
        enriched_at: str,
        enrichment_model: str,
    ) -> None:
        self.topics = topics
        self.sentiment = sentiment
        self.entities = entities
        self.categories = categories
        self.enriched_at = enriched_at
        self.enrichment_model = enrichment_model

    def to_dict(self) -> Dict[str, Any]:
        return {
            "topics": self.topics,
            "sentiment": self.sentiment,
            "entities": self.entities,
            "categories": self.categories,
            "enriched_at": self.enriched_at,
            "enrichment_model": self.enrichment_model,
        }


class EnrichmentPipeline:
    """Extracts structured metadata from memory content using an LLM."""

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def enrich(self, content: str, context: Optional[str] = None) -> Dict[str, Any]:
        """Extract topics, sentiment, entities, categories from content.

        Returns enrichment dict ready to store in metadata["enrichment"].
        Raises on LLM failure — caller must handle.
        """
        if not self.llm.check_api_key():
            raise RuntimeError("API key not configured")

        prompt = build_extraction_prompt(content, context)
        response = self.llm.complete(prompt)
        result = self._parse_and_validate(response)
        result["enriched_at"] = datetime.now(timezone.utc).isoformat()
        result["enrichment_model"] = self.llm.model
        return result

    def _parse_and_validate(self, response: str) -> Dict[str, Any]:
        """Parse LLM JSON response and validate/sanitize fields.

        Best-effort: returns partial results for malformed responses.
        """
        result: Dict[str, Any] = {
            "topics": [],
            "sentiment": {"label": "neutral", "score": 0.0},
            "entities": [],
            "categories": [],
        }

        try:
            # Strip markdown code fences if present
            text = response.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:])
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            data = json.loads(text)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Enrichment: malformed JSON response: %s", e)
            return result

        # Topics: list of 1-5 lowercase strings
        if isinstance(data.get("topics"), list):
            topics = [
                str(t).lower().strip()
                for t in data["topics"]
                if isinstance(t, str) and t.strip()
            ]
            result["topics"] = topics[:5]

        # Sentiment: {label, score}
        if isinstance(data.get("sentiment"), dict):
            sent = data["sentiment"]
            label = str(sent.get("label", "neutral")).lower()
            if label not in VALID_SENTIMENTS:
                label = "neutral"
            score = sent.get("score", 0.0)
            try:
                score = float(score)
                score = max(-1.0, min(1.0, score))  # clamp
            except (TypeError, ValueError):
                score = 0.0
            result["sentiment"] = {"label": label, "score": score}

        # Entities: list of {name, type}
        if isinstance(data.get("entities"), list):
            entities = []
            for e in data["entities"]:
                if not isinstance(e, dict):
                    continue
                name = str(e.get("name", "")).strip()
                etype = str(e.get("type", "concept")).lower().strip()
                if not name:
                    continue
                if etype not in VALID_ENTITY_TYPES:
                    etype = "concept"
                entities.append({"name": name, "type": etype})
            result["entities"] = entities

        # Categories: list of 1-3 from fixed set
        if isinstance(data.get("categories"), list):
            categories = [
                str(c).lower().strip()
                for c in data["categories"]
                if isinstance(c, str) and str(c).lower().strip() in VALID_CATEGORIES
            ]
            result["categories"] = categories[:3]

        return result
```

### 4.2 Validation Rules

| Field | Rule | Fallback |
|-------|------|----------|
| `topics` | Must be list of strings, lowercased, max 5 | Empty list |
| `sentiment.label` | Must be `positive`/`negative`/`neutral` | `"neutral"` |
| `sentiment.score` | Float, clamped to `[-1.0, 1.0]` | `0.0` |
| `entities[].name` | Non-empty string | Skipped |
| `entities[].type` | Must be from valid set | `"concept"` |
| `categories` | Must be from fixed set, max 3 | Empty list |

### 4.3 JSON Fence Stripping

LLMs frequently wrap JSON responses in ````json ... ``` `` fences. The parser strips these before `json.loads()`. This handles the most common formatting issue without complex heuristics.

---

## 5. Extraction Prompt

### 5.1 `src/lore/enrichment/prompts.py`

```python
"""Extraction prompt templates for metadata enrichment."""

from __future__ import annotations

from typing import Optional

_EXTRACTION_TEMPLATE = """\
Extract structured metadata from the following text. Return a JSON object with these fields:

- "topics": list of 1-5 topic keywords (lowercase). What is this text about?
- "sentiment": {{"label": "positive"|"negative"|"neutral", "score": float from -1.0 to 1.0}}
- "entities": list of {{"name": string, "type": string}} where type is one of: person, tool, project, platform, organization, concept, language, framework
- "categories": list of 1-3 categories from this set: infrastructure, architecture, debugging, workflow, learning, preference, incident, convention, planning, documentation, testing, security, performance, other

Text:
\"\"\"
{content}
\"\"\"
{context_section}
Return ONLY valid JSON. No explanation."""

_CONTEXT_SECTION = """
Additional context:
\"\"\"
{context}
\"\"\""""


def build_extraction_prompt(content: str, context: Optional[str] = None) -> str:
    """Build the extraction prompt for a memory's content."""
    context_section = ""
    if context:
        context_section = _CONTEXT_SECTION.format(context=context)
    return _EXTRACTION_TEMPLATE.format(
        content=content,
        context_section=context_section,
    )
```

### 5.2 Prompt Design Decisions

| Decision | Rationale |
|----------|-----------|
| Fixed prompt, not customizable | V1 simplicity. Custom prompts are a future concern. |
| Request JSON-only output | Minimizes post-processing. Combined with `temperature=0.0`, produces consistent output. |
| Category set hardcoded in prompt | Ensures LLM output maps to known values. Easier to validate. |
| Entity types in prompt | Same reasoning. Constrained output = better validation success rate. |

---

## 6. Storage Schema

### 6.1 No Migration Required

Enrichment data is stored in the existing `metadata` JSONB field under the `enrichment` key. The `Memory.metadata` field is already `Optional[Dict[str, Any]]` and supports arbitrary JSON.

```python
# Stored in memory.metadata["enrichment"]
{
    "topics": ["deployment", "kubernetes", "scaling"],
    "sentiment": {"label": "negative", "score": -0.7},
    "entities": [
        {"name": "Kubernetes", "type": "tool"},
        {"name": "AWS", "type": "platform"},
    ],
    "categories": ["infrastructure", "incident"],
    "enriched_at": "2026-03-06T14:30:00+00:00",
    "enrichment_model": "gpt-4o-mini"
}
```

### 6.2 Namespacing

The `enrichment` key is reserved and namespaced within `metadata`. User-supplied metadata keys will not conflict unless the user explicitly sets `metadata["enrichment"]`, which is documented as reserved.

### 6.3 Storage Behavior by Backend

| Backend | Behavior |
|---------|----------|
| SQLite (`SqliteStore`) | `metadata` is stored as JSON text. No changes needed — `json.dumps()`/`json.loads()` handles nested dicts. |
| In-Memory (`MemoryStore`) | Stores `Memory` dataclass directly. No changes needed. |
| HTTP (`HttpStore`) | Serializes `metadata` as JSON in request body. No changes needed. |
| PostgreSQL (server) | `meta` JSONB column. JSONB supports nested objects natively. JSONB operators enable server-side filtering (P2). |

---

## 7. Integration with `Lore` Class

### 7.1 Constructor Changes (`src/lore/lore.py`)

Add enrichment configuration to the `Lore.__init__()` signature:

```python
class Lore:
    def __init__(
        self,
        # ... existing params ...

        # NEW — Enrichment config
        enrichment: bool = False,
        enrichment_model: str = "gpt-4o-mini",
        enrichment_provider: Optional[str] = None,
    ) -> None:
        # ... existing init ...

        # Enrichment pipeline (optional)
        self._enrichment_pipeline: Optional[EnrichmentPipeline] = None
        if enrichment:
            from lore.enrichment.llm import LLMClient
            from lore.enrichment.pipeline import EnrichmentPipeline

            llm = LLMClient(model=enrichment_model, provider=enrichment_provider)
            self._enrichment_pipeline = EnrichmentPipeline(llm)
```

**Environment variable overrides** (checked before constructor values):

```python
import os

enrichment = (
    os.environ.get("LORE_ENRICHMENT_ENABLED", "").lower() in ("true", "1", "yes")
    or enrichment
)
enrichment_model = os.environ.get("LORE_ENRICHMENT_MODEL", enrichment_model)
```

**Lazy import:** `from lore.enrichment.llm import LLMClient` is inside the `if enrichment:` block. Users who don't enable enrichment never import litellm.

### 7.2 `remember()` Integration

Insert enrichment **after** redaction and embedding, **before** `Memory` construction:

```python
def remember(self, content: str, *, metadata=None, **kwargs) -> str:
    # ... existing: validation, redaction, embedding ...

    # --- Enrichment (after redaction, before save) ---
    if self._enrichment_pipeline:
        try:
            enrichment = self._enrichment_pipeline.enrich(
                content, context=kwargs.get("context")
            )
            if metadata is None:
                metadata = {}
            metadata["enrichment"] = enrichment
        except Exception as e:
            logger.warning("Enrichment failed, saving without: %s", e)

    # ... existing: construct Memory, store.save() ...
```

**Critical invariant:** The `try/except Exception` around enrichment ensures that any failure (network, API, parsing, missing key) is caught and logged. The memory is always saved.

**Ordering:**
1. Validation (type, tier, confidence)
2. Security scan + redaction (PII masking, secret blocking)
3. Embedding (compute vector from redacted content)
4. **Enrichment** (LLM sees redacted content only)
5. Construct Memory object
6. `store.save()`

Enrichment runs after redaction so the LLM never sees raw PII/secrets.

### 7.3 `recall()` Enrichment Filters

Add filter parameters to `recall()` and `_recall_local()`:

```python
def recall(
    self,
    query: str,
    *,
    # ... existing params ...
    topic: Optional[str] = None,
    sentiment: Optional[str] = None,
    entity: Optional[str] = None,
    category: Optional[str] = None,
) -> List[RecallResult]:
```

**Filter implementation in `_recall_local()`:** Post-retrieval filtering applied after cosine scoring but before ranking/limiting. To compensate for filtered-out results, over-fetch by 3x when enrichment filters are active:

```python
def _recall_local(self, query_vec, *, topic=None, sentiment=None,
                  entity=None, category=None, limit=5, **kwargs):
    # ... existing: get candidates, compute cosine, score ...

    has_enrichment_filters = any([topic, sentiment, entity, category])

    results.sort(key=lambda r: r.score, reverse=True)

    if has_enrichment_filters:
        # Over-fetch then filter
        pool = results[:limit * 3]
        filtered = [
            r for r in pool
            if self._matches_enrichment_filters(r.memory, topic, sentiment, entity, category)
        ]
        top_results = filtered[:limit]
    else:
        top_results = results[:limit]

    # ... existing: access tracking ...
    return top_results
```

**`_matches_enrichment_filters()` helper:**

```python
def _matches_enrichment_filters(
    self, memory: Memory, topic, sentiment, entity, category,
) -> bool:
    enrichment = (memory.metadata or {}).get("enrichment", {})
    if not enrichment:
        return False  # Unenriched memories excluded when filters active

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

**When no enrichment filters are specified:** All memories (enriched or not) are included normally. Zero regression for existing users.

### 7.4 `enrich_memories()` — Batch Enrichment

New method on the `Lore` class:

```python
def enrich_memories(
    self,
    memory_ids: Optional[List[str]] = None,
    *,
    project: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Batch-enrich existing memories.

    Args:
        memory_ids: Specific IDs to enrich. If None, enrich all unenriched.
        project: Filter to project (when memory_ids is None).
        force: Re-enrich memories that already have enrichment data.

    Returns:
        {"enriched": int, "skipped": int, "failed": int, "errors": [str]}
    """
    if not self._enrichment_pipeline:
        raise RuntimeError(
            "Enrichment not enabled. Set enrichment=True in Lore config."
        )

    if memory_ids:
        memories = [self._store.get(mid) for mid in memory_ids]
        memories = [m for m in memories if m is not None]
    else:
        memories = self._store.list(project=project, limit=10000)

    results = {"enriched": 0, "skipped": 0, "failed": 0, "errors": []}

    for memory in memories:
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
            results["errors"].append(f"{memory.id}: {e}")

    return results
```

**Design decisions:**
- Sequential processing (no parallelism in V1) — simpler, avoids rate limit issues.
- `limit=10000` prevents unbounded queries. Sufficient for V1 scale.
- `force=True` re-enriches — useful after model upgrades.
- Errors are collected, not raised — batch continues on failure.

---

## 8. MCP Server Integration

### 8.1 New `enrich` Tool (`src/lore/mcp/server.py`)

```python
@mcp.tool(
    description=(
        "Enrich memories with LLM-extracted metadata (topics, sentiment, entities, categories). "
        "USE THIS WHEN: you want to add structured metadata to existing memories for better filtering. "
        "Requires enrichment to be enabled in Lore config with a valid LLM API key."
    ),
)
def enrich(
    memory_id: Optional[str] = None,
    all: bool = False,
    project: Optional[str] = None,
    force: bool = False,
) -> str:
    """Enrich memories with LLM-extracted metadata."""
    try:
        lore = _get_lore()
        if memory_id:
            result = lore.enrich_memories(memory_ids=[memory_id], force=force)
        elif all:
            result = lore.enrich_memories(project=project, force=force)
        else:
            return "Provide memory_id or set all=True."

        return (
            f"Enrichment complete: {result['enriched']} enriched, "
            f"{result['skipped']} skipped, {result['failed']} failed."
        )
    except RuntimeError as e:
        return str(e)
    except Exception as e:
        return f"Enrichment failed: {e}"
```

### 8.2 Updated `recall` Tool

Add enrichment filter parameters:

```python
@mcp.tool(...)
def recall(
    query: str,
    tags: Optional[List[str]] = None,
    type: Optional[str] = None,
    tier: Optional[str] = None,
    limit: int = 5,
    repo_path: Optional[str] = None,
    topic: Optional[str] = None,       # NEW
    sentiment: Optional[str] = None,   # NEW
    entity: Optional[str] = None,      # NEW
    category: Optional[str] = None,    # NEW
) -> str:
```

Pass new params through to `lore.recall()`.

### 8.3 Updated Recall Output Format

When enrichment data is present, include it in the formatted output:

```
Memory 1  (importance: 0.92, score: 0.85, id: abc123, type: lesson, tier: long)
Topics: deployment, kubernetes | Sentiment: negative (-0.7)
Entities: Kubernetes (tool), AWS (platform)
Categories: infrastructure, incident
Content: The Kubernetes deployment failed because...
Tags:    k8s, production
```

Implementation in the recall output formatting loop:

```python
# After existing content/tags lines
enrichment = (mem.metadata or {}).get("enrichment", {})
if enrichment:
    if enrichment.get("topics"):
        parts = [f"Topics: {', '.join(enrichment['topics'])}"]
        if enrichment.get("sentiment"):
            s = enrichment["sentiment"]
            parts.append(f"Sentiment: {s['label']} ({s['score']:+.1f})")
        lines.append(" | ".join(parts))
    if enrichment.get("entities"):
        ents = [f"{e['name']} ({e['type']})" for e in enrichment["entities"]]
        lines.append(f"Entities: {', '.join(ents)}")
    if enrichment.get("categories"):
        lines.append(f"Categories: {', '.join(enrichment['categories'])}")
```

### 8.4 Updated `_get_lore()` for Enrichment Config

The `_get_lore()` factory must pass enrichment config from environment variables:

```python
def _get_lore() -> Lore:
    global _lore
    if _lore is not None:
        return _lore

    project = os.environ.get("LORE_PROJECT") or None
    store_type = os.environ.get("LORE_STORE", "local")

    enrichment = os.environ.get("LORE_ENRICHMENT_ENABLED", "").lower() in ("true", "1", "yes")
    enrichment_model = os.environ.get("LORE_ENRICHMENT_MODEL", "gpt-4o-mini")

    kwargs = {
        "project": project,
        "enrichment": enrichment,
        "enrichment_model": enrichment_model,
    }

    if store_type == "remote":
        kwargs.update(store="remote", api_url=..., api_key=...)

    _lore = Lore(**kwargs)
    return _lore
```

---

## 9. CLI Integration

### 9.1 New `enrich` Subcommand (`src/lore/cli.py`)

```python
# In build_parser()
p = sub.add_parser("enrich", help="Enrich memories with LLM-extracted metadata")
p.add_argument("memory_id", nargs="?", default=None, help="Memory ID to enrich")
p.add_argument("--all", action="store_true", help="Enrich all unenriched memories")
p.add_argument("--project", default=None, help="Filter to project (with --all)")
p.add_argument("--force", action="store_true", help="Re-enrich already enriched memories")
p.add_argument(
    "--model", default=None,
    help="LLM model for enrichment (default: gpt-4o-mini)",
)
```

```python
def cmd_enrich(args: argparse.Namespace) -> None:
    from lore import Lore

    model = args.model or os.environ.get("LORE_ENRICHMENT_MODEL", "gpt-4o-mini")
    lore = Lore(db_path=args.db, enrichment=True, enrichment_model=model)

    if args.memory_id:
        result = lore.enrich_memories(memory_ids=[args.memory_id], force=args.force)
    elif args.all:
        result = lore.enrich_memories(project=args.project, force=args.force)
    else:
        print("Provide a memory ID or use --all", file=sys.stderr)
        lore.close()
        sys.exit(1)

    lore.close()
    print(
        f"Enriched: {result['enriched']}, "
        f"Skipped: {result['skipped']}, "
        f"Failed: {result['failed']}"
    )
    if result["errors"]:
        for err in result["errors"]:
            print(f"  Error: {err}", file=sys.stderr)
```

### 9.2 Updated `recall` Subcommand

Add enrichment filter flags:

```python
# In build_parser(), recall subparser
p.add_argument("--topic", default=None, help="Filter by enrichment topic")
p.add_argument("--sentiment", default=None, choices=["positive", "negative", "neutral"],
               help="Filter by sentiment label")
p.add_argument("--entity", default=None, help="Filter by entity name")
p.add_argument("--category", default=None, help="Filter by category")
```

Pass through to `lore.recall()`:

```python
results = lore.recall(
    args.query,
    type=args.type, tier=tier, tags=tags, limit=args.limit,
    topic=getattr(args, "topic", None),
    sentiment=getattr(args, "sentiment", None),
    entity=getattr(args, "entity", None),
    category=getattr(args, "category", None),
)
```

### 9.3 Updated `recall` Output

Show enrichment data in CLI recall output when present:

```python
for r in results:
    print(f"[{r.score:.3f}] {r.memory.id} ({r.memory.type}, {r.memory.tier})")
    print(f"  {r.memory.content[:200]}")
    enrichment = (r.memory.metadata or {}).get("enrichment", {})
    if enrichment.get("topics"):
        print(f"  Topics: {', '.join(enrichment['topics'])}")
    if r.memory.tags:
        print(f"  Tags: {', '.join(r.memory.tags)}")
    print()
```

### 9.4 Updated `memories` (list) Output

Add topics column when enrichment data exists:

```python
# In cmd_memories
for m in memories:
    enrichment = (m.metadata or {}).get("enrichment", {})
    topics = ", ".join(enrichment.get("topics", [])) if enrichment else "-"
    # Include topics in table output
```

---

## 10. Server API Integration (P2)

### 10.1 Search Endpoint Enrichment Filters

Update `POST /api/v1/memories/search` (or `/v1/lessons/search`) to accept enrichment filters:

```json
{
    "query": "deployment issues",
    "topic": "kubernetes",
    "sentiment": "negative"
}
```

**Server-side JSONB filtering:**

```sql
WHERE (meta->'enrichment'->'topics' ? %(topic)s OR %(topic)s IS NULL)
  AND (meta->'enrichment'->'sentiment'->>'label' = %(sentiment)s OR %(sentiment)s IS NULL)
  AND (EXISTS (
      SELECT 1 FROM jsonb_array_elements(meta->'enrichment'->'entities') e
      WHERE lower(e->>'name') = lower(%(entity)s)
  ) OR %(entity)s IS NULL)
  AND (meta->'enrichment'->'categories' ? %(category)s OR %(category)s IS NULL)
```

### 10.2 New `POST /api/v1/enrich` Endpoint

For server-side batch enrichment (requires server to have enrichment configured):

```python
@router.post("/v1/enrich")
async def enrich_endpoint(
    memory_ids: Optional[List[str]] = None,
    project: Optional[str] = None,
    force: bool = False,
):
    # Requires enrichment to be configured on server
    result = lore.enrich_memories(memory_ids=memory_ids, project=project, force=force)
    return result
```

**Note:** Server-side enrichment is P2. In V1, enrichment happens client-side only.

---

## 11. Error Handling

### 11.1 Error Matrix

| Scenario | Where Caught | Behavior |
|----------|-------------|----------|
| `enrichment=True`, litellm not installed | `LLMClient.__init__()` | `ImportError` with install instructions. Raised at `Lore()` construction. |
| `enrichment=True`, no API key | `LLMClient.check_api_key()` | Warning logged once, enrichment skipped. Memory saved without enrichment. |
| LLM API call fails (network, rate limit, 500) | `remember()` try/except | Warning logged, enrichment skipped. Memory saved without enrichment. |
| LLM returns malformed JSON | `_parse_and_validate()` | Best-effort partial parse. Missing fields get defaults (empty list, neutral sentiment). |
| LLM returns unexpected values | Validation in `_parse_and_validate()` | Clamped/corrected: sentiment score to [-1,1], unknown entity types to "concept", unknown categories filtered out. |
| Batch: some memories fail | `enrich_memories()` per-memory try/except | Error recorded in results, processing continues. Summary returned. |
| Enrichment enabled on recall filters | `_matches_enrichment_filters()` | Unenriched memories silently excluded when filters active. Included normally when no filters. |

### 11.2 Logging Strategy

```python
import logging
logger = logging.getLogger("lore.enrichment")
```

| Level | When |
|-------|------|
| `WARNING` | Missing API key (once), LLM failure, malformed JSON |
| `DEBUG` | Enrichment success with timing, prompt sent, response received |

No `ERROR`-level logging from enrichment — it's always best-effort.

---

## 12. Configuration

### 12.1 Constructor Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enrichment` | `bool` | `False` | Enable enrichment pipeline |
| `enrichment_model` | `str` | `"gpt-4o-mini"` | LLM model identifier |
| `enrichment_provider` | `Optional[str]` | `None` | Provider override (auto-detected from model name) |

### 12.2 Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LORE_ENRICHMENT_ENABLED` | `false` | Enable enrichment |
| `LORE_ENRICHMENT_MODEL` | `gpt-4o-mini` | Model for enrichment |
| `OPENAI_API_KEY` | — | Required for OpenAI models |
| `ANTHROPIC_API_KEY` | — | Required for Anthropic models |
| `GOOGLE_API_KEY` | — | Required for Google models |

**Precedence:** Environment variable > constructor argument. This allows MCP server deployments to configure enrichment without code changes.

### 12.3 Optional Dependency

```toml
# pyproject.toml
[project.optional-dependencies]
enrichment = ["litellm>=1.0"]
```

Install: `pip install lore-memory[enrichment]`

---

## 13. Testing Strategy

### 13.1 Unit Tests (`tests/test_enrichment.py`)

All LLM calls are mocked. No real API calls in tests.

| Test | Validates |
|------|-----------|
| `test_build_extraction_prompt` | Prompt includes content, optional context section |
| `test_build_extraction_prompt_no_context` | Context section omitted when None |
| `test_parse_valid_json` | Well-formed JSON → correct enrichment dict |
| `test_parse_json_with_code_fences` | ````json ... ``` `` → stripped and parsed |
| `test_parse_malformed_json` | Invalid JSON → empty defaults returned |
| `test_parse_partial_json` | JSON with some valid, some missing fields → partial result |
| `test_validate_topics_max_five` | More than 5 topics → truncated to 5 |
| `test_validate_topics_lowercase` | Mixed case → all lowercased |
| `test_validate_sentiment_clamp` | Score 2.5 → clamped to 1.0 |
| `test_validate_sentiment_invalid_label` | Unknown label → "neutral" |
| `test_validate_entities_invalid_type` | Unknown type → "concept" |
| `test_validate_categories_from_fixed_set` | Unknown categories filtered out |
| `test_validate_categories_max_three` | More than 3 → truncated |

### 13.2 LLM Client Tests (`tests/test_enrichment_llm.py`)

| Test | Validates |
|------|-----------|
| `test_detect_provider_openai` | "gpt-4o-mini" → "openai" |
| `test_detect_provider_anthropic` | "claude-3-haiku" → "anthropic" |
| `test_detect_provider_google` | "gemini-pro" → "google" |
| `test_detect_provider_unknown` | Unknown prefix → "openai" (fallback) |
| `test_check_api_key_present` | Key in env → True |
| `test_check_api_key_missing` | Key not in env → False, warning logged once |
| `test_check_api_key_warn_once` | Multiple calls → warning only on first |
| `test_import_error_no_litellm` | litellm not installed → ImportError with message |

### 13.3 Integration Tests (`tests/test_enrichment_integration.py`)

| Test | Validates |
|------|-----------|
| `test_remember_with_enrichment` | Mock LLM → metadata.enrichment populated |
| `test_remember_enrichment_failure` | Mock LLM raises → memory saved without enrichment |
| `test_remember_enrichment_disabled` | `enrichment=False` → no LLM call, no enrichment key |
| `test_remember_enrichment_no_api_key` | No key → enrichment skipped, memory saved |
| `test_recall_filter_by_topic` | Only memories with matching topic returned |
| `test_recall_filter_by_sentiment` | Only matching sentiment returned |
| `test_recall_filter_by_entity` | Only memories with matching entity returned |
| `test_recall_filter_by_category` | Only matching category returned |
| `test_recall_filter_multiple` | Combined filters (topic + sentiment) |
| `test_recall_filter_excludes_unenriched` | Unenriched memories excluded when filters active |
| `test_recall_no_filter_includes_unenriched` | No filters → all memories included (no regression) |
| `test_recall_filter_case_insensitive` | "Kubernetes" matches "kubernetes" |

### 13.4 Batch Enrichment Tests

| Test | Validates |
|------|-----------|
| `test_enrich_memories_all` | All unenriched memories get enrichment |
| `test_enrich_memories_skip_enriched` | Already-enriched skipped (force=False) |
| `test_enrich_memories_force` | force=True re-enriches all |
| `test_enrich_memories_by_ids` | Only specified IDs enriched |
| `test_enrich_memories_partial_failure` | Some fail → others still enriched, errors collected |
| `test_enrich_memories_not_enabled` | enrichment=False → RuntimeError |
| `test_enrich_memories_by_project` | Only memories in project enriched |

### 13.5 Regression Tests

- Run entire existing test suite with `enrichment=False` (default) — all 590+ tests must pass unchanged.
- Verify `recall()` without enrichment filters returns identical results to pre-F6.

### 13.6 Mock Strategy

```python
from unittest.mock import patch, MagicMock

MOCK_ENRICHMENT_RESPONSE = json.dumps({
    "topics": ["deployment", "kubernetes"],
    "sentiment": {"label": "negative", "score": -0.5},
    "entities": [{"name": "Kubernetes", "type": "tool"}],
    "categories": ["infrastructure"],
})

# Patch litellm.completion to return mock response
@patch("lore.enrichment.llm.litellm")
def test_remember_with_enrichment(mock_litellm):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = MOCK_ENRICHMENT_RESPONSE
    mock_litellm.completion.return_value = mock_response
    # ... test code ...
```

---

## 14. File Change Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `src/lore/enrichment/__init__.py` | **New** | Public exports: `EnrichmentPipeline`, `LLMClient`, `EnrichmentResult` |
| `src/lore/enrichment/llm.py` | **New** | `LLMClient` with litellm, provider detection, API key check |
| `src/lore/enrichment/prompts.py` | **New** | `build_extraction_prompt()` with template |
| `src/lore/enrichment/pipeline.py` | **New** | `EnrichmentPipeline` with `enrich()`, JSON parse/validate |
| `src/lore/lore.py` | Modify | Constructor (enrichment params), `remember()` (enrichment call), `recall()` and `_recall_local()` (filter params), `enrich_memories()` (new method), `_matches_enrichment_filters()` (new helper) |
| `src/lore/mcp/server.py` | Modify | New `enrich` tool, `recall` filter params + output formatting, `_get_lore()` enrichment config |
| `src/lore/cli.py` | Modify | New `enrich` subcommand, `recall` filter flags, recall/memories output formatting |
| `pyproject.toml` | Modify | Add `[enrichment]` optional dependency |
| `tests/test_enrichment.py` | **New** | Unit tests for pipeline, parsing, validation |
| `tests/test_enrichment_llm.py` | **New** | Unit tests for LLM client |
| `tests/test_enrichment_integration.py` | **New** | Integration tests for remember/recall with enrichment |

---

## 15. Implementation Order

Recommended sequence for implementation stories:

1. **S1: Enrichment module** — Create `src/lore/enrichment/` package: `llm.py`, `prompts.py`, `pipeline.py`, `__init__.py`. Unit tests for prompt building, JSON parsing, validation.
2. **S2: Lore integration** — Constructor changes, `remember()` enrichment call, `enrich_memories()` batch method. Integration tests with mocked LLM.
3. **S3: Recall filtering** — Add filter params to `recall()` and `_recall_local()`, implement `_matches_enrichment_filters()`, over-fetch logic. Filter tests.
4. **S4: MCP tools** — New `enrich` tool, `recall` filter params, output formatting, `_get_lore()` config.
5. **S5: CLI** — New `enrich` subcommand, `recall` filter flags, output formatting.
6. **S6: pyproject.toml** — Add `[enrichment]` optional dependency.
7. **S7: Server API** (P2) — JSONB filtering on search endpoint, `/enrich` endpoint.

S1 has no dependencies. S2 depends on S1. S3 depends on S2. S4-S6 depend on S2-S3 and can be parallelized. S7 is P2 and can be deferred.

---

## 16. Pipeline Extensibility (Future)

While not implemented in F6, the architecture is designed for F2 (Fact Extraction) and F9 (Dialog Classification) to plug in:

```python
# Future: EnrichmentPipeline becomes a chain of steps
class EnrichmentPipeline:
    def __init__(self, steps: List[EnrichmentStep]):
        self.steps = steps

    def enrich(self, content, context=None):
        result = {}
        for step in self.steps:
            result.update(step.run(content, context))
        return result
```

For F6, the pipeline has a single step (metadata extraction). The `enrich()` method returns a flat dict that can be extended with additional keys by future steps without breaking the existing schema.
