# Architecture: F9 — Dialog Classification

**Version:** 1.0
**Author:** Architect Agent
**Date:** 2026-03-06
**PRD:** `_bmad-output/planning-artifacts/f09-dialog-classification-prd.md`
**Depends on:** None (standalone). Shares LLM provider with F6 (Metadata Enrichment).

---

## 1. Overview

This document specifies how to implement dialog classification — a system that tags memories with **intent**, **domain**, and **emotion** labels plus confidence scores. The system uses an LLM when available (via a shared provider abstraction) and falls back to rule-based keyword/pattern matching otherwise.

### Architecture Principles

1. **Pluggable classifiers** — LLM and rule-based share an abstract interface. Swapping is transparent.
2. **Graceful degradation** — LLM failure falls back to rule-based. Classification failure never blocks `remember()`.
3. **Shared LLM provider** — F9 creates `src/lore/llm/` if F6 hasn't landed. F6 reuses it (or vice versa).
4. **Opt-in by default** — Classification is disabled unless explicitly enabled. Zero overhead when off.
5. **Post-filter recall** — Classification filters are applied after vector similarity search, matching the F6 enrichment pattern.

---

## 2. Classification Types & Schemas

### 2.1 Taxonomy Constants (`src/lore/classify/taxonomies.py`)

```python
from typing import Tuple

INTENT_LABELS: Tuple[str, ...] = (
    "question",      # Asking for information ("How do I deploy?")
    "statement",     # Declaring a fact ("The build is broken")
    "instruction",   # Directing action ("Run tests before merging")
    "preference",    # Personal choice/convention ("I always use bun")
    "observation",   # Noting without judgment ("Deploy took 12 min")
    "decision",      # Recording a choice ("We chose Postgres over MySQL")
)

DOMAIN_LABELS: Tuple[str, ...] = (
    "technical",       # Code, tools, infra, debugging
    "personal",        # Habits, non-work topics
    "business",        # Strategy, metrics, stakeholders
    "creative",        # Design, writing, brainstorming
    "administrative",  # Process, scheduling, org
)

EMOTION_LABELS: Tuple[str, ...] = (
    "neutral",      # No strong emotional signal
    "frustrated",   # Annoyance, blockers
    "excited",      # Enthusiasm, positive energy
    "curious",      # Exploration, wondering
    "confident",    # Certainty, conviction
    "uncertain",    # Doubt, hedging
)
```

### 2.2 Classification Dataclass (`src/lore/classify/base.py`)

```python
from dataclasses import dataclass, field
from typing import Dict

@dataclass
class Classification:
    """Result of classifying a piece of text."""
    intent: str
    domain: str
    emotion: str
    confidence: Dict[str, float] = field(default_factory=dict)
    # confidence keys: "intent", "domain", "emotion" — each 0.0-1.0
    low_confidence: bool = False  # P1: set when min(confidences) < threshold
```

**Validation:** The `Classification` factory helper validates labels against taxonomy tuples and clamps confidence to `[0.0, 1.0]`:

```python
def make_classification(
    intent: str,
    domain: str,
    emotion: str,
    confidence: Dict[str, float],
) -> Classification:
    """Create a validated Classification. Raises ValueError for unknown labels."""
    if intent not in INTENT_LABELS:
        raise ValueError(f"Unknown intent: {intent!r}")
    if domain not in DOMAIN_LABELS:
        raise ValueError(f"Unknown domain: {domain!r}")
    if emotion not in EMOTION_LABELS:
        raise ValueError(f"Unknown emotion: {emotion!r}")
    clamped = {k: max(0.0, min(1.0, v)) for k, v in confidence.items()}
    return Classification(intent=intent, domain=domain, emotion=emotion, confidence=clamped)
```

### 2.3 Storage Schema — `metadata.classification`

Stored in the existing `metadata` JSONB field (no schema migration):

```json
{
    "classification": {
        "intent": "preference",
        "domain": "technical",
        "emotion": "confident",
        "confidence": {
            "intent": 0.92,
            "domain": 0.88,
            "emotion": 0.75
        }
    }
}
```

**Optional P1 fields:**
- `"low_confidence": true` — added when `min(confidences) < classification_confidence_threshold`
- `"classifier": "llm"` or `"classifier": "rules"` — which backend produced the result (for diagnostics)

No new database columns. No migration. The `metadata` TEXT/JSONB column already stores arbitrary dicts.

---

## 3. Classifier Interface & Implementations

### 3.1 Abstract Base (`src/lore/classify/base.py`)

```python
from abc import ABC, abstractmethod

class Classifier(ABC):
    """Abstract classifier — implemented by LLM and rule-based backends."""

    @abstractmethod
    def classify(self, text: str) -> Classification:
        """Classify text by intent, domain, and emotion."""
        ...
```

### 3.2 LLM Classifier (`src/lore/classify/llm.py`)

```python
from lore.llm import LLMProvider
from lore.classify.base import Classifier, Classification, make_classification
from lore.classify.rules import RuleBasedClassifier

class LLMClassifier(Classifier):
    """LLM-backed classification with rule-based fallback."""

    def __init__(self, provider: LLMProvider):
        self._provider = provider
        self._fallback = RuleBasedClassifier()

    def classify(self, text: str) -> Classification:
        try:
            response = self._provider.complete(
                self._build_prompt(text),
                max_tokens=200,
            )
            return self._parse_response(response)
        except Exception:
            # LLM failure → fall back to rules
            return self._fallback.classify(text)

    def _build_prompt(self, text: str) -> str:
        return CLASSIFY_PROMPT.format(content=text)

    def _parse_response(self, response: str) -> Classification:
        """Parse JSON from LLM response. Falls back to rules on parse failure."""
        import json
        # Strip markdown code fences if present
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            raise ValueError("Malformed JSON from LLM")

        return make_classification(
            intent=data.get("intent", "statement"),
            domain=data.get("domain", "technical"),
            emotion=data.get("emotion", "neutral"),
            confidence=data.get("confidence", {
                "intent": 0.5, "domain": 0.5, "emotion": 0.5
            }),
        )
```

**LLM Prompt** (single call, all three axes):

```python
CLASSIFY_PROMPT = """Classify the following text along three axes.

Text: "{content}"

Respond with ONLY a JSON object (no markdown, no explanation):
{{
  "intent": one of [question, statement, instruction, preference, observation, decision],
  "domain": one of [technical, personal, business, creative, administrative],
  "emotion": one of [neutral, frustrated, excited, curious, confident, uncertain],
  "confidence": {{
    "intent": 0.0-1.0,
    "domain": 0.0-1.0,
    "emotion": 0.0-1.0
  }}
}}"""
```

**Per-axis validation:** If the LLM returns a valid JSON but with an unknown label for one axis, that single axis falls back to rule-based classification (not the entire result). This maximizes the value from the LLM call:

```python
def _parse_response(self, response: str) -> Classification:
    # ... parse JSON ...
    intent = data.get("intent")
    domain = data.get("domain")
    emotion = data.get("emotion")

    # Per-axis fallback for invalid labels
    if intent not in INTENT_LABELS:
        intent = self._fallback._classify_intent(original_text)
    if domain not in DOMAIN_LABELS:
        domain = self._fallback._classify_domain(original_text)
    if emotion not in EMOTION_LABELS:
        emotion = self._fallback._classify_emotion(original_text)
    # ...
```

### 3.3 Rule-Based Classifier (`src/lore/classify/rules.py`)

```python
import re
from lore.classify.base import Classifier, Classification
from lore.classify.taxonomies import INTENT_LABELS, DOMAIN_LABELS, EMOTION_LABELS

_MATCHED_CONFIDENCE = 0.6
_DEFAULT_CONFIDENCE = 0.3

class RuleBasedClassifier(Classifier):
    """Keyword/pattern matching fallback — no LLM required."""

    INTENT_PATTERNS = {
        "question": [
            r"\?\s*$",
            r"^(how|what|why|when|where|who|can|should|is|are|do|does)\b",
        ],
        "instruction": [
            r"^(always|never|make sure|don't|do not|ensure|run|use|set)\b",
        ],
        "preference": [
            r"\b(prefer|always use|i like|i want|my choice|i use)\b",
        ],
        "decision": [
            r"\b(decided|we chose|going with|settled on|decision)\b",
        ],
        "observation": [
            r"\b(noticed|observed|seems|appears|looks like|today)\b",
        ],
        # "statement" is the default fallback — no patterns
    }

    DOMAIN_PATTERNS = {
        "technical": [
            r"\b(code|bug|api|deploy|test|git|docker|server|database|"
            r"function|class|error|config|build|compile|debug|CI|CD|"
            r"kubernetes|k8s|npm|pip|yarn|bun|webpack|vite)\b",
        ],
        "business": [
            r"\b(revenue|customer|stakeholder|okr|metric|strategy|"
            r"roadmap|budget|kpi|roi|market|sales|growth)\b",
        ],
        "creative": [
            r"\b(design|ui|ux|brand|color|layout|write|content|"
            r"story|illustration|prototype|wireframe|mockup)\b",
        ],
        "administrative": [
            r"\b(meeting|schedule|process|policy|review|approval|"
            r"deadline|standup|retro|sprint|planning)\b",
        ],
        # "personal" is the default fallback — no patterns
    }

    EMOTION_PATTERNS = {
        "frustrated": [
            r"\b(annoying|broken|keeps? (failing|breaking)|ugh|"
            r"frustrat|stupid|damn|hate|worst|terrible|horrible)\b",
        ],
        "excited": [
            r"\b(amazing|awesome|love|great|fantastic|excited|"
            r"finally|incredible|brilliant|perfect|beautiful)\b",
        ],
        "curious": [
            r"\b(wonder|curious|interesting|how come|what if|"
            r"explore|intriguing|fascinating)\b",
        ],
        "confident": [
            r"\b(definitely|certainly|sure|confident|absolutely|"
            r"clearly|obviously|without doubt|100%)\b",
        ],
        "uncertain": [
            r"\b(maybe|perhaps|not sure|might|possibly|i think|"
            r"unsure|unclear|probably|seems like)\b",
        ],
        # "neutral" is the default fallback — no patterns
    }

    def classify(self, text: str) -> Classification:
        intent, intent_conf = self._classify_axis(text, self.INTENT_PATTERNS, "statement")
        domain, domain_conf = self._classify_axis(text, self.DOMAIN_PATTERNS, "personal")
        emotion, emotion_conf = self._classify_axis(text, self.EMOTION_PATTERNS, "neutral")
        return Classification(
            intent=intent,
            domain=domain,
            emotion=emotion,
            confidence={
                "intent": intent_conf,
                "domain": domain_conf,
                "emotion": emotion_conf,
            },
        )

    def _classify_axis(
        self, text: str, patterns: dict, default: str
    ) -> tuple[str, float]:
        """Match text against patterns for a single axis.

        Returns (label, confidence). If multiple labels match, returns the
        one with the most pattern hits. Default label gets _DEFAULT_CONFIDENCE.
        """
        text_lower = text.lower().strip()
        best_label = default
        best_hits = 0

        for label, regexes in patterns.items():
            hits = sum(
                1 for regex in regexes
                if re.search(regex, text_lower, re.IGNORECASE)
            )
            if hits > best_hits:
                best_hits = hits
                best_label = label

        confidence = _MATCHED_CONFIDENCE if best_hits > 0 else _DEFAULT_CONFIDENCE
        return best_label, confidence

    # Expose per-axis methods for LLMClassifier's per-axis fallback
    def _classify_intent(self, text: str) -> str:
        label, _ = self._classify_axis(text, self.INTENT_PATTERNS, "statement")
        return label

    def _classify_domain(self, text: str) -> str:
        label, _ = self._classify_axis(text, self.DOMAIN_PATTERNS, "personal")
        return label

    def _classify_emotion(self, text: str) -> str:
        label, _ = self._classify_axis(text, self.EMOTION_PATTERNS, "neutral")
        return label
```

---

## 4. Shared LLM Provider (`src/lore/llm/`)

### 4.1 Module Structure

```
src/lore/llm/
    __init__.py    # Exports: LLMProvider, create_provider
    base.py        # LLMProvider ABC
    openai.py      # OpenAIProvider (OpenAI-compatible: OpenAI, Anthropic via proxy, local)
```

### 4.2 Abstract Provider (`src/lore/llm/base.py`)

```python
from abc import ABC, abstractmethod

class LLMProvider(ABC):
    """Abstract LLM provider — shared between F6 and F9."""

    @abstractmethod
    def complete(self, prompt: str, *, max_tokens: int = 200) -> str:
        """Send a prompt and return the response text."""
        ...
```

### 4.3 OpenAI-Compatible Provider (`src/lore/llm/openai.py`)

```python
import httpx
from lore.llm.base import LLMProvider

class OpenAIProvider(LLMProvider):
    """OpenAI-compatible API provider (works with OpenAI, local models, proxies)."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
    ):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")

    def complete(self, prompt: str, *, max_tokens: int = 200) -> str:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": 0.1,  # low temp for consistent classification
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
```

Uses `httpx` (already a dependency via `mcp` SDK) — no new deps.

### 4.4 Provider Factory (`src/lore/llm/__init__.py`)

```python
from lore.llm.base import LLMProvider
from lore.llm.openai import OpenAIProvider

def create_provider(
    provider: str = "openai",
    model: str = "gpt-4o-mini",
    api_key: str | None = None,
    base_url: str | None = None,
) -> LLMProvider:
    """Create an LLM provider from config."""
    if provider == "openai":
        if not api_key:
            raise ValueError("api_key required for OpenAI provider")
        return OpenAIProvider(
            api_key=api_key,
            model=model,
            base_url=base_url or "https://api.openai.com/v1",
        )
    raise ValueError(f"Unknown LLM provider: {provider!r}")
```

### 4.5 Configuration

Environment variables (shared with F6):

| Variable | Default | Description |
|----------|---------|-------------|
| `LORE_LLM_PROVIDER` | `openai` | Provider type |
| `LORE_LLM_MODEL` | `gpt-4o-mini` | Model identifier |
| `LORE_LLM_API_KEY` | — | API key |
| `LORE_LLM_BASE_URL` | — | Custom base URL (for local models) |

F6 uses the same env vars. Whichever feature lands first creates the `src/lore/llm/` module.

---

## 5. Dual-Backend System & Fallback Strategy

### 5.1 Backend Selection

```
classify=True + LLM configured  → LLMClassifier (with rule-based fallback)
classify=True + no LLM          → RuleBasedClassifier
classify=False                   → None (no classification)
```

### 5.2 Fallback Triggers

The LLM classifier falls back to rule-based in these scenarios:

| Scenario | Scope | Behavior |
|----------|-------|----------|
| LLM API call fails (network, timeout, rate limit) | Full | Entire classification uses rules |
| LLM returns non-JSON response | Full | Entire classification uses rules |
| LLM returns valid JSON with unknown label on one axis | Per-axis | Only that axis uses rules; others keep LLM result |
| LLM returns valid JSON with missing confidence | Partial | Default confidence 0.5 for missing axes |
| No LLM configured | Full | Always uses rules |

### 5.3 Fallback Confidence Markers

When fallback is used, the classification includes diagnostic info:

```python
# Full fallback
classification.confidence  # all values ≤ 0.6 (rule-based max)

# The "classifier" field in metadata (P1) indicates which backend ran
metadata["classification"]["classifier"] = "rules"  # or "llm"
```

### 5.4 Rule Trigger Summary

**Intent rules** (first match wins):

| Label | Trigger |
|-------|---------|
| `question` | Ends with `?` or starts with wh-word/modal verb |
| `instruction` | Starts with imperative verb (always, never, run, use, set, ensure, don't) |
| `preference` | Contains "prefer", "always use", "I like", "I want", "my choice" |
| `decision` | Contains "decided", "we chose", "going with", "settled on" |
| `observation` | Contains "noticed", "observed", "seems", "appears", "looks like" |
| `statement` | Default — nothing else matched |

**Domain rules:**

| Label | Trigger |
|-------|---------|
| `technical` | Contains code/infra terms (code, bug, api, deploy, test, git, docker, etc.) |
| `business` | Contains business terms (revenue, customer, stakeholder, OKR, metric, etc.) |
| `creative` | Contains design/content terms (design, UI, UX, brand, color, layout, etc.) |
| `administrative` | Contains process terms (meeting, schedule, deadline, sprint, etc.) |
| `personal` | Default — nothing else matched |

**Emotion rules:**

| Label | Trigger |
|-------|---------|
| `frustrated` | Contains frustration terms (annoying, broken, keeps failing, ugh, hate, etc.) |
| `excited` | Contains enthusiasm terms (amazing, awesome, love, great, finally, etc.) |
| `curious` | Contains exploration terms (wonder, curious, interesting, what if, etc.) |
| `confident` | Contains certainty terms (definitely, certainly, sure, absolutely, etc.) |
| `uncertain` | Contains doubt terms (maybe, perhaps, not sure, might, probably, etc.) |
| `neutral` | Default — nothing else matched |

---

## 6. Integration with Lore Core

### 6.1 Constructor Changes (`src/lore/lore.py`)

```python
class Lore:
    def __init__(
        self,
        # ... existing params ...
        classify: bool = False,
        classification_confidence_threshold: float = 0.5,  # P1
        llm_provider: Optional[str] = None,
        llm_model: Optional[str] = None,
        llm_api_key: Optional[str] = None,
        llm_base_url: Optional[str] = None,
    ) -> None:
        # ... existing init ...

        # Classification setup
        self._classifier: Optional[Classifier] = None
        self._classification_threshold = classification_confidence_threshold

        if classify or _env_bool("LORE_CLASSIFY"):
            llm_prov = llm_provider or os.environ.get("LORE_LLM_PROVIDER")
            llm_key = llm_api_key or os.environ.get("LORE_LLM_API_KEY")
            llm_mod = llm_model or os.environ.get("LORE_LLM_MODEL", "gpt-4o-mini")
            llm_url = llm_base_url or os.environ.get("LORE_LLM_BASE_URL")

            if llm_prov and llm_key:
                from lore.llm import create_provider
                provider = create_provider(
                    provider=llm_prov, model=llm_mod,
                    api_key=llm_key, base_url=llm_url,
                )
                self._classifier = LLMClassifier(provider)
            else:
                self._classifier = RuleBasedClassifier()
```

**`_env_bool` helper:**

```python
def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("true", "1", "yes")
```

### 6.2 Integration with `remember()`

Classification runs after redaction (LLM sees redacted content) and before `Memory` construction:

```python
def remember(self, content: str, *, ..., classification: Optional[Dict] = None) -> str:
    # ... existing: validation, redaction, embedding ...

    # Classification (after redaction, before save)
    if classification is not None:
        # R18 (P2): user-supplied classification override
        meta = dict(metadata) if metadata else {}
        meta["classification"] = classification
        metadata = meta
    elif self._classifier:
        try:
            cls = self._classifier.classify(content)
            meta = dict(metadata) if metadata else {}
            cls_dict = {
                "intent": cls.intent,
                "domain": cls.domain,
                "emotion": cls.emotion,
                "confidence": cls.confidence,
            }
            # P1: low confidence marker
            min_conf = min(cls.confidence.values()) if cls.confidence else 0.0
            if min_conf < self._classification_threshold:
                cls_dict["low_confidence"] = True
            meta["classification"] = cls_dict
            metadata = meta
        except Exception:
            pass  # Classification failure never blocks storage

    memory = Memory(id=str(ULID()), content=content, ..., metadata=metadata, ...)
    self._store.save(memory)
    return memory.id
```

**Key design decisions:**
- Classification runs after redaction → LLM only sees sanitized content
- Classification failure is silently caught → memory stored without classification
- User-supplied `classification=` dict skips auto-classification (P2: R18)
- Metadata dict is copied (`dict(metadata)`) to avoid mutating caller's dict

### 6.3 Integration with `recall()`

Add optional filter parameters, post-filter after scoring:

```python
def recall(
    self,
    query: str,
    *,
    # ... existing params ...
    intent: Optional[str] = None,
    domain: Optional[str] = None,
    emotion: Optional[str] = None,
) -> List[RecallResult]:
    # ... existing: cleanup, embedding, search ...
    # Pass filters to _recall_local
    results = self._recall_local(
        query_vec, ..., intent=intent, domain=domain, emotion=emotion,
    )
    return results
```

In `_recall_local()`, apply filters after scoring and before truncating to limit:

```python
def _recall_local(self, query_vec, *, ..., intent=None, domain=None, emotion=None):
    # ... existing: candidate retrieval, scoring ...
    results.sort(key=lambda r: r.score, reverse=True)

    # Classification post-filter
    if intent or domain or emotion:
        results = [
            r for r in results
            if self._matches_classification(r.memory, intent, domain, emotion)
        ]

    top_results = results[:limit]
    # ... access tracking ...
    return top_results
```

**Filter logic:**

```python
def _matches_classification(self, memory: Memory, intent, domain, emotion) -> bool:
    """Check if memory's classification matches the given filters.

    Memories without classification data are excluded when filters are active.
    """
    cls = (memory.metadata or {}).get("classification", {})
    if not cls:
        return False  # Unclassified memories excluded when filtering
    if intent and cls.get("intent") != intent:
        return False
    if domain and cls.get("domain") != domain:
        return False
    if emotion and cls.get("emotion") != emotion:
        return False
    return True
```

**Over-fetch strategy:** When classification filters are active, the initial candidate pool should be larger to compensate for post-filtering. Fetch `limit * 3` candidates from the store, score and filter, then trim to `limit`:

```python
effective_limit = limit * 3 if (intent or domain or emotion) else limit
# ... scoring loop uses all candidates regardless ...
# ... post-filter reduces to <= effective results ...
top_results = results[:limit]  # final trim
```

### 6.4 `Lore.classify()` — Standalone Method

```python
def classify(self, text: str) -> Classification:
    """Classify text by intent, domain, and emotion.

    Works regardless of whether classification is enabled on remember().
    Uses LLM if configured, falls back to rule-based classification.
    """
    if self._classifier:
        return self._classifier.classify(text)
    # No classifier configured — create a one-off rule-based classifier
    return RuleBasedClassifier().classify(text)
```

This always works — even if `classify=False` on the constructor. The `classify()` method is usable standalone without enabling auto-classification on `remember()`.

---

## 7. Enrichment Pipeline Integration

### 7.1 Relationship with F6

When both F6 (enrichment) and F9 (classification) are enabled:

```
remember(content)
  ├─ redaction
  ├─ F9: classification → metadata["classification"]
  ├─ F6: enrichment → metadata["enrichment"]
  ├─ embedding
  └─ store.save()
```

Classification runs first because:
1. It's cheaper (shorter prompt, less output)
2. Classification result could inform enrichment in the future (e.g., skip entity extraction for questions)
3. If classification fails, enrichment still runs independently

### 7.2 Independent Operation

F9 works without F6. F6 works without F9. Both share `src/lore/llm/` but have no runtime dependency on each other:

```
F9 only:  metadata = {"classification": {...}}
F6 only:  metadata = {"enrichment": {...}}
Both:     metadata = {"classification": {...}, "enrichment": {...}}
Neither:  metadata = None (or user-supplied)
```

### 7.3 Shared Provider Instance

When both features are enabled and use the same LLM config, they should share a single `LLMProvider` instance to reuse connection pooling:

```python
# In Lore.__init__:
self._llm_provider: Optional[LLMProvider] = None

if llm_prov and llm_key:
    self._llm_provider = create_provider(...)

if classify_enabled and self._llm_provider:
    self._classifier = LLMClassifier(self._llm_provider)
elif classify_enabled:
    self._classifier = RuleBasedClassifier()

# Future F6 integration:
# if enrichment_enabled and self._llm_provider:
#     self._enrichment_pipeline = EnrichmentPipeline(self._llm_provider)
```

---

## 8. API & CLI

### 8.1 MCP Tool: `classify` (new)

```python
@mcp.tool(
    description=(
        "Classify a piece of text by intent, domain, and emotion. "
        "Returns structured classification without storing anything. "
        "USE THIS WHEN: you want to understand the nature of a piece of text "
        "before storing it, or to analyze conversation patterns."
    ),
)
def classify(text: str) -> str:
    """Classify text — does not store anything."""
    try:
        lore = _get_lore()
        result = lore.classify(text)
        return (
            f"Intent:  {result.intent:<14} ({result.confidence.get('intent', 0):.0%})\n"
            f"Domain:  {result.domain:<14} ({result.confidence.get('domain', 0):.0%})\n"
            f"Emotion: {result.emotion:<14} ({result.confidence.get('emotion', 0):.0%})"
        )
    except Exception as e:
        return f"Classification failed: {e}"
```

### 8.2 MCP Tool: `recall` (updated)

Add `intent`, `domain`, `emotion` optional parameters:

```python
@mcp.tool(...)
def recall(
    query: str,
    tags: Optional[List[str]] = None,
    type: Optional[str] = None,
    tier: Optional[str] = None,
    limit: int = 5,
    repo_path: Optional[str] = None,
    intent: Optional[str] = None,     # NEW
    domain: Optional[str] = None,     # NEW
    emotion: Optional[str] = None,    # NEW
) -> str:
    # ... pass intent/domain/emotion to lore.recall() ...
```

Update recall output to show classification when present:

```
Memory 1  (importance: 0.87, score: 0.74, id: abc123, type: preference, tier: long)
Classification: preference / technical / confident
Content: I always use bun instead of npm...
```

### 8.3 MCP Tool: `list_memories` (updated — P1: R14)

Add `intent`, `domain`, `emotion` filter parameters. Filter in Python after listing:

```python
def list_memories(
    type=None, tier=None, project=None, limit=None,
    intent=None, domain=None, emotion=None,  # NEW (P1)
):
    memories = lore.list_memories(type=type, tier=tier, project=project, limit=limit)
    if intent or domain or emotion:
        memories = [
            m for m in memories
            if _matches_classification(m, intent, domain, emotion)
        ]
    # ... format output ...
```

### 8.4 CLI: `lore classify` (new)

```python
def cmd_classify(args: argparse.Namespace) -> None:
    lore = _get_lore(args.db)
    result = lore.classify(args.text)
    lore.close()

    if args.json:
        import json
        print(json.dumps({
            "intent": result.intent,
            "domain": result.domain,
            "emotion": result.emotion,
            "confidence": result.confidence,
        }, indent=2))
    else:
        print(f"Intent:  {result.intent:<14} ({result.confidence.get('intent', 0):.0%})")
        print(f"Domain:  {result.domain:<14} ({result.confidence.get('domain', 0):.0%})")
        print(f"Emotion: {result.emotion:<14} ({result.confidence.get('emotion', 0):.0%})")
```

Argparse subcommand:

```python
p_classify = sub.add_parser("classify", help="Classify text by intent/domain/emotion")
p_classify.add_argument("text", help="Text to classify")
p_classify.add_argument("--json", action="store_true", help="Output as JSON")
p_classify.add_argument("--db", default=None)
p_classify.set_defaults(func=cmd_classify)
```

### 8.5 CLI: `lore recall` (updated)

Add `--intent`, `--domain`, `--emotion` flags:

```python
p_recall.add_argument("--intent", choices=INTENT_LABELS, default=None)
p_recall.add_argument("--domain", choices=DOMAIN_LABELS, default=None)
p_recall.add_argument("--emotion", choices=EMOTION_LABELS, default=None)
```

---

## 9. Recall Query Building for Filters

### 9.1 Local Store (SQLite)

Classification filters are applied in Python after vector search (post-filter), not in SQL. This is consistent with how tag filtering works in `_recall_local()`.

**Flow:**

```
1. store.list(project, type, tier) → all candidate memories
2. filter: expired, tags, min_confidence, embeddings (existing)
3. vectorized cosine scoring (existing)
4. sort by score desc
5. NEW: post-filter by classification (intent/domain/emotion)
6. trim to limit
7. access tracking on top_k
```

### 9.2 Remote Store (HTTP/Postgres)

For the server-side search endpoint, classification filters can be pushed to SQL using JSONB operators:

```sql
-- Filter by intent
AND metadata->'classification'->>'intent' = 'preference'

-- Filter by domain
AND metadata->'classification'->>'domain' = 'technical'

-- Filter by emotion
AND metadata->'classification'->>'emotion' = 'confident'
```

The `HttpStore.search()` method passes the filter params to the server API. The server constructs the appropriate SQL WHERE clause.

### 9.3 Index Considerations

For large-scale Postgres deployments with heavy classification filtering, a GIN index on `metadata` is recommended:

```sql
CREATE INDEX IF NOT EXISTS idx_lessons_metadata_gin ON lessons USING gin(metadata jsonb_path_ops);
```

For SQLite, the post-filter approach is fast enough for the expected corpus sizes (< 10K memories).

---

## 10. Performance

### 10.1 Latency Budget

| Operation | LLM Backend | Rule-Based |
|-----------|-------------|------------|
| Single classification | 200-500ms (gpt-4o-mini) | < 1ms |
| Classification on remember() | +200-500ms | +1ms |
| Classification filter on recall() | +0ms (post-filter only) | +0ms |
| Standalone classify() | 200-500ms | < 1ms |

### 10.2 Avoiding Redundant LLM Calls

1. **No re-classification on recall** — Classification is stored at `remember()` time. Recall filters operate on stored metadata, no LLM call needed.
2. **Single prompt, all axes** — One LLM call classifies all three axes simultaneously. Not three separate calls.
3. **Rule-based is free** — When no LLM is configured, classification adds negligible latency.

### 10.3 Batch Classification (P2: R17)

```python
def classify_batch(self, texts: List[str]) -> List[Classification]:
    """Classify multiple texts. Uses single LLM call when possible."""
    if not texts:
        return []
    if isinstance(self._classifier, LLMClassifier):
        # Build a batch prompt with numbered texts
        return self._classifier.classify_batch(texts)
    return [self.classify(t) for t in texts]
```

Batch prompt format:

```
Classify each of the following texts. Return a JSON array.

Text 1: "..."
Text 2: "..."
...

[{"intent": ..., "domain": ..., "emotion": ..., "confidence": {...}}, ...]
```

This reduces N LLM calls to 1 for batch operations. Useful for retroactive classification of existing memories.

### 10.4 Caching

Classification caching is **not** implemented in V1. Each `remember()` classifies independently. Rationale:
- Memories are rarely identical — cache hit rate would be near zero
- The primary cost-saving measure (single-prompt all-axes) is already in place
- Adding a cache adds complexity with minimal benefit

Future consideration: hash-based cache for `classify()` standalone calls where the same text might be classified multiple times.

---

## 11. Interaction with F2 (Fact Extraction)

### 11.1 Classification as Input Signal

F2 (Fact Extraction) can use classification to prioritize what to extract:

```python
# In F2's extraction pipeline (future):
if classification and classification["intent"] in ("statement", "decision", "preference"):
    # High-value for fact extraction — extract aggressively
    facts = self._extract_facts(content, depth="thorough")
elif classification and classification["intent"] in ("question", "observation"):
    # Lower priority — extract conservatively
    facts = self._extract_facts(content, depth="surface")
else:
    facts = self._extract_facts(content, depth="standard")
```

### 11.2 Pipeline Order

When all three intelligence features are enabled:

```
remember(content)
  ├─ redaction (existing)
  ├─ F9: classification → metadata["classification"]
  ├─ F6: enrichment → metadata["enrichment"]
  ├─ F2: fact extraction → metadata["facts"] (uses classification as hint)
  ├─ embedding (existing)
  └─ store.save()
```

Classification runs first because it's cheapest and its output can inform downstream steps. This is a pipeline ordering convention, not a hard dependency — each step works independently.

### 11.3 No Circular Dependencies

```
F9 (classification) → produces metadata["classification"]
F6 (enrichment) → produces metadata["enrichment"], may read classification
F2 (fact extraction) → produces metadata["facts"], may read classification
```

Data flows one direction. No feature depends on another feature's output to function.

---

## 12. Module Dependency Graph

```
src/lore/llm/                    (NEW — shared LLM provider)
    __init__.py                   # create_provider(), LLMProvider
    base.py                       # LLMProvider ABC
    openai.py                     # OpenAIProvider

src/lore/classify/               (NEW — classification engine)
    __init__.py                   # Classifier, Classification, LLMClassifier, RuleBasedClassifier
    base.py                       # Classifier ABC, Classification dataclass, make_classification()
    llm.py                        # LLMClassifier (depends on lore.llm, lore.classify.rules)
    rules.py                      # RuleBasedClassifier (no external deps)
    taxonomies.py                 # INTENT_LABELS, DOMAIN_LABELS, EMOTION_LABELS

src/lore/lore.py                  # classify param, _classifier init, remember() integration,
                                  # recall() filters, classify() method
src/lore/mcp/server.py            # classify tool, recall filter params, output formatting
src/lore/cli.py                   # classify subcommand, recall --intent/--domain/--emotion
```

**Dependency flow:**

```
taxonomies.py  ←  base.py  ←  rules.py
                     ↑            ↑
                  llm.py ─────────┘ (fallback)
                     ↑
                  lore/llm/base.py

lore.py ← classify/ (Classifier, Classification, LLMClassifier, RuleBasedClassifier)
        ← lore/llm/ (create_provider)

mcp/server.py ← lore.py (Lore.classify, Lore.recall)
cli.py ← lore.py (Lore.classify)
       ← classify/taxonomies.py (for argparse choices)
```

---

## 13. File Change Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `src/lore/llm/__init__.py` | **New** | `create_provider()`, `LLMProvider` export |
| `src/lore/llm/base.py` | **New** | `LLMProvider` ABC |
| `src/lore/llm/openai.py` | **New** | `OpenAIProvider` — OpenAI-compatible HTTP client |
| `src/lore/classify/__init__.py` | **New** | Package exports: `Classifier`, `Classification`, `LLMClassifier`, `RuleBasedClassifier` |
| `src/lore/classify/base.py` | **New** | `Classifier` ABC, `Classification` dataclass, `make_classification()` |
| `src/lore/classify/llm.py` | **New** | `LLMClassifier` with LLM prompt, JSON parsing, per-axis fallback |
| `src/lore/classify/rules.py` | **New** | `RuleBasedClassifier` with keyword/regex patterns for all 3 axes |
| `src/lore/classify/taxonomies.py` | **New** | `INTENT_LABELS`, `DOMAIN_LABELS`, `EMOTION_LABELS` constants |
| `src/lore/lore.py` | Modify | Add `classify`/`llm_*` params to constructor, classification step in `remember()`, filter params in `recall()`, `classify()` method, `_matches_classification()` helper |
| `src/lore/mcp/server.py` | Modify | Add `classify` tool, add `intent`/`domain`/`emotion` params to `recall` and `list_memories`, update recall output format |
| `src/lore/cli.py` | Modify | Add `classify` subcommand, add `--intent`/`--domain`/`--emotion` to `recall` |
| `tests/test_classification.py` | **New** | Unit tests for `Classification`, `make_classification()`, taxonomy validation |
| `tests/test_classification_llm.py` | **New** | Unit tests for `LLMClassifier` with mocked LLM (valid, malformed, error) |
| `tests/test_classification_rules.py` | **New** | Unit tests for `RuleBasedClassifier` — all patterns for all 3 axes |
| `tests/test_classification_integration.py` | **New** | Integration: remember with classify → recall with filters → correct results |
| `tests/test_llm_provider.py` | **New** | Unit tests for `OpenAIProvider` with mocked HTTP |

---

## 14. Testing Strategy

### 14.1 Unit Tests — Classification Engine

| Test | File | Validates |
|------|------|-----------|
| `test_make_classification_valid` | `test_classification.py` | Valid labels + confidence → Classification |
| `test_make_classification_invalid_intent` | `test_classification.py` | Unknown intent → ValueError |
| `test_make_classification_clamps_confidence` | `test_classification.py` | Confidence > 1.0 clamped to 1.0 |
| `test_taxonomy_completeness` | `test_classification.py` | All labels are lowercase, unique, non-empty |

### 14.2 Unit Tests — Rule-Based Classifier

| Test | File | Validates |
|------|------|-----------|
| `test_question_mark` | `test_classification_rules.py` | "What is X?" → intent=question |
| `test_question_wh_word` | `test_classification_rules.py` | "How do I deploy?" → intent=question |
| `test_instruction_imperative` | `test_classification_rules.py` | "Always run tests" → intent=instruction |
| `test_preference_keywords` | `test_classification_rules.py` | "I always use bun" → intent=preference |
| `test_decision_keywords` | `test_classification_rules.py` | "We decided to use Postgres" → intent=decision |
| `test_observation_keywords` | `test_classification_rules.py` | "I noticed the build takes 3 min" → intent=observation |
| `test_statement_default` | `test_classification_rules.py` | "The sky is blue" → intent=statement (default) |
| `test_technical_domain` | `test_classification_rules.py` | "Fix the API bug" → domain=technical |
| `test_business_domain` | `test_classification_rules.py` | "Revenue grew 20%" → domain=business |
| `test_personal_default` | `test_classification_rules.py` | "I like coffee" → domain=personal (default) |
| `test_frustrated_emotion` | `test_classification_rules.py` | "This keeps breaking" → emotion=frustrated |
| `test_excited_emotion` | `test_classification_rules.py` | "This is amazing!" → emotion=excited |
| `test_neutral_default` | `test_classification_rules.py` | "The meeting is at 3" → emotion=neutral |
| `test_confidence_matched` | `test_classification_rules.py` | Pattern match → confidence=0.6 |
| `test_confidence_default` | `test_classification_rules.py` | No match → confidence=0.3 |
| `test_empty_text` | `test_classification_rules.py` | "" → defaults (statement/personal/neutral) |
| `test_whitespace_only` | `test_classification_rules.py` | "   " → defaults |
| `test_case_insensitive` | `test_classification_rules.py` | "HOW DO I?" → question |

### 14.3 Unit Tests — LLM Classifier

| Test | File | Validates |
|------|------|-----------|
| `test_valid_llm_response` | `test_classification_llm.py` | Well-formed JSON → correct Classification |
| `test_llm_with_markdown_fences` | `test_classification_llm.py` | ` ```json ... ``` ` stripped and parsed |
| `test_llm_malformed_json` | `test_classification_llm.py` | Garbage response → falls back to rules |
| `test_llm_unknown_label` | `test_classification_llm.py` | Unknown intent → per-axis fallback |
| `test_llm_api_error` | `test_classification_llm.py` | Network error → full rule-based fallback |
| `test_llm_missing_confidence` | `test_classification_llm.py` | No confidence in response → defaults to 0.5 |
| `test_llm_prompt_format` | `test_classification_llm.py` | Prompt includes text and valid instructions |

### 14.4 Integration Tests

| Test | File | Validates |
|------|------|-----------|
| `test_remember_with_classification` | `test_classification_integration.py` | classify=True → metadata.classification stored |
| `test_remember_without_classification` | `test_classification_integration.py` | classify=False → no classification metadata |
| `test_remember_classification_failure` | `test_classification_integration.py` | LLM error → memory saved without classification |
| `test_recall_filter_intent` | `test_classification_integration.py` | intent="preference" → only preferences returned |
| `test_recall_filter_multi` | `test_classification_integration.py` | domain+emotion → AND filter |
| `test_recall_no_filter` | `test_classification_integration.py` | No filters → all results (backward compat) |
| `test_recall_excludes_unclassified` | `test_classification_integration.py` | Filter active → unclassified memories excluded |
| `test_classify_standalone` | `test_classification_integration.py` | lore.classify() works with classify=False |
| `test_classify_mcp_tool` | `test_classification_integration.py` | MCP classify tool returns formatted output |
| `test_user_override_classification` | `test_classification_integration.py` | classification={...} on remember() skips auto |
| `test_low_confidence_marker` | `test_classification_integration.py` | Below threshold → low_confidence=True |

### 14.5 Edge Cases

| Test | Validates |
|------|-----------|
| Empty text ("") | Defaults to statement/personal/neutral with low confidence |
| Very long text (10K+ chars) | LLM handles within token limit; rules scan full text |
| Unicode/emoji text | Regex patterns work with unicode; LLM handles naturally |
| Text with only special characters | Defaults with low confidence |
| Text in non-English language | LLM classifies (model-dependent); rules fall to defaults |
| Multiple matching patterns | Highest-hit-count label wins (rule-based) |

### 14.6 LLM Provider Tests

| Test | File | Validates |
|------|------|-----------|
| `test_openai_provider_success` | `test_llm_provider.py` | Mocked HTTP 200 → response text extracted |
| `test_openai_provider_error` | `test_llm_provider.py` | HTTP 429/500 → raises exception |
| `test_create_provider_openai` | `test_llm_provider.py` | create_provider("openai") → OpenAIProvider |
| `test_create_provider_unknown` | `test_llm_provider.py` | create_provider("unknown") → ValueError |
| `test_create_provider_no_key` | `test_llm_provider.py` | No API key → ValueError |

---

## 15. Implementation Order

Recommended sequence for implementation stories:

1. **S1: Taxonomies + Base Types** — Create `src/lore/classify/taxonomies.py`, `base.py` (Classification dataclass, Classifier ABC, make_classification) + unit tests
2. **S2: LLM Provider** — Create `src/lore/llm/` (ABC, OpenAIProvider, create_provider) + unit tests. Skip if F6 already created this module.
3. **S3: Rule-Based Classifier** — Create `src/lore/classify/rules.py` + comprehensive pattern tests
4. **S4: LLM Classifier** — Create `src/lore/classify/llm.py` with prompt, parsing, per-axis fallback + unit tests with mocked LLM
5. **S5: Lore Integration** — Constructor changes, `remember()` classification, `recall()` filters, `classify()` method + integration tests
6. **S6: MCP + CLI** — `classify` tool, recall filter params, CLI subcommand
7. **S7: P1 Features** — Confidence threshold, low_confidence marker, list_memories filtering, classification in recall output
8. **S8: P2 Features** — Batch classify, user override, classification stats

S1-S2 have no dependencies and can be done in parallel. S3-S4 depend on S1. S5 depends on S1-S4. S6-S8 depend on S5.

---

## 16. Backward Compatibility

| Concern | Mitigation |
|---------|-----------|
| New `recall()` params | All optional, default None. Existing calls unchanged. |
| New `remember()` params | All optional. `classify` defaults to False. Zero behavior change. |
| `metadata.classification` key | New key in existing JSONB field. No conflicts with existing metadata. |
| No LLM configured | Classification disabled by default. No error, no LLM calls. |
| Memories without classification | Excluded from filtered recall. Included in unfiltered recall. |
| New `src/lore/llm/` module | No existing module conflicts. If F6 creates it first, F9 imports from it. |
| No new required dependencies | `httpx` already available via `mcp` SDK dependency. |
| Existing tests | All pass unchanged — classification is off by default. |
