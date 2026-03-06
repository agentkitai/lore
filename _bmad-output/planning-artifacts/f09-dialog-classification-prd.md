# PRD: F9 — Dialog Classification

**Version:** 1.0
**Author:** John (Product Manager)
**Date:** 2026-03-06
**Status:** Draft
**Phase:** 2 — Intelligence Layer
**Depends on:** None (standalone), but shares LLM provider with F6 (Metadata Enrichment)

---

## 1. Problem Statement

When Lore stores a memory, it captures the raw content and a user-specified `type` (general, code, lesson, etc.), but it has no understanding of the *nature* of what was said. Was it a question? A preference? An instruction? Is it about technical work or personal life? Was the user frustrated or excited?

This lack of classification creates three problems:

1. **Recall is query-only.** Agents can't ask "show me all my technical preferences" or "what questions have I asked about deployment?" — they can only do freeform semantic search.
2. **No emotional context.** A frustrated debugging note and a confident architectural decision are treated identically. Agents can't prioritize confident statements over uncertain ones.
3. **No intent awareness.** Preferences ("I always use bun") and observations ("the build takes 3 minutes") are stored the same way. Agents can't filter for actionable preferences vs. informational observations.

Dialog classification adds structured metadata that makes memories queryable along three new axes: intent, domain, and emotion.

## 2. Solution Overview

Add a classification step that analyzes incoming memory content and tags it with:

- **Intent:** What kind of statement is this? (question, statement, instruction, preference, observation, decision)
- **Domain:** What area does it relate to? (technical, personal, business, creative, administrative)
- **Emotion:** What's the emotional tone? (neutral, frustrated, excited, curious, confident, uncertain)

Classification runs on `remember()` (if enabled), stores results in `metadata.classification`, and is filterable on `recall()`. It also works as a standalone tool/command for classifying arbitrary text.

## 3. Goals

1. **Structured classification** — Every memory (when enabled) gets an `intent`, `domain`, and `emotion` label with confidence scores.
2. **Filterable recall** — `recall('query', intent='preference', domain='technical')` returns only matching memories.
3. **Standalone classify tool** — Classify arbitrary text without storing it (MCP tool + CLI command).
4. **LLM-powered with rule-based fallback** — Uses LLM when available, falls back to keyword/pattern matching when not.
5. **Shared LLM provider** — Reuses the same LLM provider abstraction as F6 (same config, same API key, same model).
6. **Independent operation** — Works without F6 enrichment pipeline. Can be the only LLM feature enabled.

## 4. Non-Goals

- **Multi-label classification** — Each axis gets exactly one label (not multiple). Keep it simple.
- **Custom classification axes** — The three axes (intent, domain, emotion) are fixed. User-defined taxonomies are out of scope.
- **Training or fine-tuning** — No custom models. Uses general-purpose LLM prompting or rule-based patterns.
- **Retroactive reclassification** — No batch reclassify of existing memories (that's an enrichment pipeline concern in F6).
- **Classification-based routing** — No automatic actions based on classification (e.g., auto-tiering by domain). That's a future concern.

## 5. Requirements

### 5.1 Must-Have (P0)

| ID | Requirement | Details |
|----|-------------|---------|
| R1 | **Classification data model** | `metadata.classification = { "intent": str, "domain": str, "emotion": str, "confidence": { "intent": float, "domain": float, "emotion": float } }` stored in existing `metadata` JSONB field. |
| R2 | **Classification on remember()** | When classification is enabled, `remember()` classifies content before storing. Classification is added to `metadata.classification`. |
| R3 | **Intent taxonomy** | Fixed set: `question`, `statement`, `instruction`, `preference`, `observation`, `decision`. |
| R4 | **Domain taxonomy** | Fixed set: `technical`, `personal`, `business`, `creative`, `administrative`. |
| R5 | **Emotion taxonomy** | Fixed set: `neutral`, `frustrated`, `excited`, `curious`, `confident`, `uncertain`. |
| R6 | **Confidence scores** | Each axis has a confidence score (0.0-1.0). Overall confidence is the minimum of the three axis confidences. |
| R7 | **Filterable recall** | `recall()` accepts optional `intent`, `domain`, `emotion` parameters. Results are post-filtered by classification match. |
| R8 | **MCP tool: classify** | New tool that classifies arbitrary text and returns the classification object. Does not store anything. |
| R9 | **CLI: lore classify** | `lore classify 'some text'` prints classification result to stdout. |
| R10 | **Rule-based fallback** | When no LLM is configured, use keyword/pattern matching for basic classification. Must handle all three axes. |
| R11 | **Opt-in configuration** | Classification is disabled by default. Enable via `Lore(classify=True)` or `LORE_CLASSIFY=true` env var. |
| R12 | **Shared LLM provider** | Uses the same LLM provider config as F6: `LORE_LLM_PROVIDER`, `LORE_LLM_MODEL`, `LORE_LLM_API_KEY`. If F6 introduces a provider abstraction, F9 reuses it. If F9 lands first, F9 creates it and F6 adopts it. |

### 5.2 Should-Have (P1)

| ID | Requirement | Details |
|----|-------------|---------|
| R13 | **Confidence threshold** | `Lore(classification_confidence_threshold=0.5)` — classifications below this confidence are stored but marked `"low_confidence": true`. |
| R14 | **list_memories filtering** | `list_memories` also accepts `intent`, `domain`, `emotion` filter parameters. |
| R15 | **Classification in recall output** | MCP `recall` tool output includes classification labels when present (e.g., `[preference, technical, confident]`). |

### 5.3 Nice-to-Have (P2)

| ID | Requirement | Details |
|----|-------------|---------|
| R16 | **Classification stats** | `stats` output includes classification distribution (count per intent, domain, emotion). |
| R17 | **Batch classify** | `Lore.classify_batch(texts: List[str])` for efficient multi-text classification in a single LLM call. |
| R18 | **Override classification** | Allow users to pass `classification={...}` to `remember()` to skip auto-classification and use their own labels. |

## 6. Detailed Design

### 6.1 Classification Data Model

Classification is stored inside the existing `metadata` dict on `Memory`:

```python
# After classification, memory.metadata looks like:
{
    "classification": {
        "intent": "preference",
        "domain": "technical",
        "emotion": "confident",
        "confidence": {
            "intent": 0.92,
            "domain": 0.88,
            "emotion": 0.75,
        }
    },
    # ... other metadata (existing or from F6 enrichment)
}
```

No schema changes needed — `metadata` is already a JSONB/JSON field. Classification is just structured data within it.

### 6.2 Classification Taxonomies

```python
INTENT_LABELS = ["question", "statement", "instruction", "preference", "observation", "decision"]
DOMAIN_LABELS = ["technical", "personal", "business", "creative", "administrative"]
EMOTION_LABELS = ["neutral", "frustrated", "excited", "curious", "confident", "uncertain"]
```

**Intent definitions:**
- `question` — Asking for information or clarification ("How do I deploy to staging?")
- `statement` — Declaring a fact or status ("The build is broken")
- `instruction` — Directing action ("Run the tests before merging")
- `preference` — Expressing a personal choice or convention ("I always use bun instead of npm")
- `observation` — Noting something without judgment ("The deploy took 12 minutes today")
- `decision` — Recording a choice made ("We decided to use Postgres over MySQL")

**Domain definitions:**
- `technical` — Code, architecture, tools, infrastructure, debugging
- `personal` — Personal preferences, habits, non-work topics
- `business` — Strategy, planning, stakeholders, metrics, OKRs
- `creative` — Design, writing, content creation, brainstorming
- `administrative` — Process, scheduling, organizational, housekeeping

**Emotion definitions:**
- `neutral` — No strong emotional signal
- `frustrated` — Annoyance, difficulty, blockers ("this keeps breaking")
- `excited` — Enthusiasm, positive energy ("this is amazing")
- `curious` — Exploration, learning, wondering ("I wonder if...")
- `confident` — Certainty, conviction ("I'm sure this is the right approach")
- `uncertain` — Doubt, hedging ("I'm not sure but maybe...")

### 6.3 LLM-Based Classification

Single LLM call with structured output prompt:

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
}}
"""
```

**LLM provider:** Uses a shared `LLMProvider` abstraction:

```python
class LLMProvider(ABC):
    """Abstract LLM provider — shared between F6 and F9."""
    @abstractmethod
    async def complete(self, prompt: str, max_tokens: int = 200) -> str:
        ...

class OpenAIProvider(LLMProvider):
    """OpenAI-compatible provider (OpenAI, Anthropic, local models via OpenAI API)."""
    ...
```

Configuration:
- `LORE_LLM_PROVIDER=openai` (default) — OpenAI-compatible API
- `LORE_LLM_MODEL=gpt-4o-mini` — cheap, fast model for classification
- `LORE_LLM_API_KEY=sk-...` — API key
- `LORE_LLM_BASE_URL=...` — optional, for local models or Anthropic

If F6 has already implemented this provider, F9 imports and reuses it. If F9 lands first, F9 creates the provider module and F6 adopts it.

### 6.4 Rule-Based Fallback

When no LLM is configured, fall back to keyword/pattern matching:

```python
class RuleBasedClassifier:
    """Keyword-based classification — no LLM required."""

    INTENT_PATTERNS = {
        "question": [r"\?$", r"^(how|what|why|when|where|who|can|should|is|are|do|does)\b"],
        "instruction": [r"^(always|never|make sure|don't|do not|ensure|run|use|set)\b"],
        "preference": [r"\b(prefer|always use|i like|i want|my choice|i use)\b"],
        "decision": [r"\b(decided|we chose|going with|settled on|decision)\b"],
        "observation": [r"\b(noticed|observed|seems|appears|looks like|today)\b"],
        "statement": [],  # default fallback
    }

    DOMAIN_PATTERNS = {
        "technical": [r"\b(code|bug|api|deploy|test|git|docker|server|database|function|class|error|config)\b"],
        "business": [r"\b(revenue|customer|stakeholder|okr|metric|strategy|roadmap|budget)\b"],
        "creative": [r"\b(design|ui|ux|brand|color|layout|write|content|story)\b"],
        "administrative": [r"\b(meeting|schedule|process|policy|review|approval|deadline)\b"],
        "personal": [],  # default fallback
    }

    EMOTION_PATTERNS = {
        "frustrated": [r"\b(annoying|broken|keeps? (failing|breaking)|ugh|frustrat|stupid|damn|hate)\b"],
        "excited": [r"\b(amazing|awesome|love|great|fantastic|excited|finally)\b"],
        "curious": [r"\b(wonder|curious|interesting|how come|what if|explore)\b"],
        "confident": [r"\b(definitely|certainly|sure|confident|absolutely|clearly)\b"],
        "uncertain": [r"\b(maybe|perhaps|not sure|might|possibly|i think|unsure)\b"],
        "neutral": [],  # default fallback
    }
```

Rule-based confidence is fixed at `0.6` for matched patterns and `0.3` for fallback defaults.

### 6.5 Classifier Interface

```python
class Classifier(ABC):
    """Abstract classifier — implemented by LLM and rule-based backends."""

    @abstractmethod
    def classify(self, text: str) -> Classification:
        ...

@dataclass
class Classification:
    intent: str
    domain: str
    emotion: str
    confidence: Dict[str, float]  # {"intent": 0.9, "domain": 0.8, "emotion": 0.7}

class LLMClassifier(Classifier):
    """LLM-backed classification."""
    def __init__(self, provider: LLMProvider):
        self.provider = provider
    ...

class RuleBasedClassifier(Classifier):
    """Keyword/pattern matching fallback."""
    ...
```

### 6.6 Integration with remember()

In `Lore.remember()`, after content is prepared but before storing:

```python
def remember(self, content, ...):
    # ... existing logic (embedding, redaction, etc.) ...

    if self._classifier:
        classification = self._classifier.classify(content)
        if memory.metadata is None:
            memory.metadata = {}
        memory.metadata["classification"] = {
            "intent": classification.intent,
            "domain": classification.domain,
            "emotion": classification.emotion,
            "confidence": classification.confidence,
        }

    self._store.save(memory)
```

### 6.7 Integration with recall()

Add optional filter parameters to `recall()`:

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
    results = self._recall_internal(query, ...)

    # Post-filter by classification
    if intent or domain or emotion:
        results = [
            r for r in results
            if self._matches_classification(r.memory, intent, domain, emotion)
        ]

    return results

def _matches_classification(self, memory, intent, domain, emotion) -> bool:
    cls = (memory.metadata or {}).get("classification", {})
    if intent and cls.get("intent") != intent:
        return False
    if domain and cls.get("domain") != domain:
        return False
    if emotion and cls.get("emotion") != emotion:
        return False
    return True
```

**Note:** Memories without classification data pass through filters (they don't match, so they're excluded). This is intentional — classification filters only return classified memories.

### 6.8 MCP Tool: classify

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
    lore = _get_lore()
    result = lore.classify(text)
    return (
        f"Intent: {result.intent} ({result.confidence['intent']:.0%})\n"
        f"Domain: {result.domain} ({result.confidence['domain']:.0%})\n"
        f"Emotion: {result.emotion} ({result.confidence['emotion']:.0%})"
    )
```

### 6.9 CLI: lore classify

```
$ lore classify 'I always use bun instead of npm for package management'
Intent:   preference  (92%)
Domain:   technical   (95%)
Emotion:  confident   (78%)

$ lore classify 'Why does this keep breaking every time I deploy?'
Intent:   question    (95%)
Domain:   technical   (88%)
Emotion:  frustrated  (85%)
```

**Flags:**
- `--json` — output as JSON instead of formatted text
- `--provider` — override LLM provider (default: use configured provider or fall back to rules)

### 6.10 Enrichment Pipeline Integration

When both F6 (enrichment) and F9 (classification) are enabled, classification runs as a step in the enrichment pipeline. The pipeline calls classification first (it's cheaper/faster), then enrichment.

When F9 is enabled alone (without F6), classification runs standalone — a single, lighter LLM call. This is explicitly supported and tested.

```python
# In Lore.__init__:
if classify and llm_provider:
    self._classifier = LLMClassifier(llm_provider)
elif classify:
    self._classifier = RuleBasedClassifier()
else:
    self._classifier = None
```

## 7. API / Interface Changes

### 7.1 Lore Constructor

```python
Lore(
    # ... existing params ...
    classify: bool = False,                       # enable classification on remember()
    classification_confidence_threshold: float = 0.5,  # P1 — low confidence marker
    # LLM config (shared with F6)
    llm_provider: Optional[str] = None,           # "openai" or None
    llm_model: Optional[str] = None,              # e.g., "gpt-4o-mini"
    llm_api_key: Optional[str] = None,
    llm_base_url: Optional[str] = None,
)
```

### 7.2 Lore.classify()

```python
def classify(self, text: str) -> Classification:
    """Classify text by intent, domain, and emotion.

    Works regardless of whether classification is enabled on remember().
    Uses LLM if configured, falls back to rule-based classification.
    """
```

### 7.3 Lore.recall() — new params

```python
def recall(
    self,
    query: str,
    *,
    # ... existing params ...
    intent: Optional[str] = None,    # filter by intent label
    domain: Optional[str] = None,    # filter by domain label
    emotion: Optional[str] = None,   # filter by emotion label
) -> List[RecallResult]:
```

### 7.4 MCP recall tool — new params

```python
@mcp.tool()
def recall(
    query: str,
    # ... existing params ...
    intent: Optional[str] = None,
    domain: Optional[str] = None,
    emotion: Optional[str] = None,
) -> str:
```

### 7.5 MCP classify tool (new)

```python
@mcp.tool()
def classify(text: str) -> str:
```

### 7.6 CLI: lore classify (new)

```
lore classify 'text to classify'
lore classify 'text' --json
```

## 8. File Changes

| File | Change |
|------|--------|
| `src/lore/classify/__init__.py` | **NEW** — Package init, exports `Classifier`, `Classification`, `LLMClassifier`, `RuleBasedClassifier` |
| `src/lore/classify/base.py` | **NEW** — `Classifier` ABC and `Classification` dataclass |
| `src/lore/classify/llm.py` | **NEW** — `LLMClassifier` implementation |
| `src/lore/classify/rules.py` | **NEW** — `RuleBasedClassifier` with keyword/pattern matching |
| `src/lore/classify/taxonomies.py` | **NEW** — `INTENT_LABELS`, `DOMAIN_LABELS`, `EMOTION_LABELS` constants and definitions |
| `src/lore/llm/__init__.py` | **NEW** — `LLMProvider` ABC (shared with F6). If F6 already created this, reuse it. |
| `src/lore/llm/openai.py` | **NEW** — `OpenAIProvider` implementation (shared with F6). |
| `src/lore/lore.py` | Add `classify` param to constructor, add `classify()` method, add classification step in `remember()`, add filter params to `recall()` |
| `src/lore/mcp/server.py` | Add `classify` tool, add `intent`/`domain`/`emotion` params to `recall` tool |
| `src/lore/cli.py` | Add `classify` subcommand |
| `tests/test_classification.py` | **NEW** — Unit tests for classification logic |
| `tests/test_classification_rules.py` | **NEW** — Unit tests for rule-based classifier |
| `tests/test_classification_integration.py` | **NEW** — Integration tests (classify on remember, filter on recall) |

## 9. Backward Compatibility

| Concern | Mitigation |
|---------|-----------|
| New `recall()` params | All optional, default None. Existing calls unchanged. |
| `metadata.classification` key | New metadata key. Doesn't conflict with existing metadata. |
| No LLM configured | Classification disabled by default. Rule-based fallback available. |
| Memories without classification | Recall with classification filters excludes unclassified memories. Document this behavior. |
| F6 provider overlap | Both features share `src/lore/llm/` module. Whichever lands first creates it; the other adopts. |

## 10. Acceptance Criteria

### Classification Engine

- [ ] AC-1: `LLMClassifier.classify(text)` returns a `Classification` with valid intent, domain, emotion labels and confidence scores.
- [ ] AC-2: `RuleBasedClassifier.classify(text)` returns valid classification using keyword/pattern matching for all three axes.
- [ ] AC-3: Rule-based classifier correctly classifies questions (ending in `?`), instructions (starting with imperative verbs), and preferences (containing "prefer", "always use", etc.).
- [ ] AC-4: Confidence scores are between 0.0 and 1.0 for all axes.
- [ ] AC-5: Invalid LLM response (malformed JSON, unknown labels) falls back to rule-based classification gracefully.

### Integration with remember()

- [ ] AC-6: With `classify=True`, `remember()` stores `metadata.classification` on the memory.
- [ ] AC-7: With `classify=False` (default), `remember()` does not classify and does not modify metadata.
- [ ] AC-8: Classification failure (LLM error) does not prevent the memory from being stored — it's stored without classification.

### Integration with recall()

- [ ] AC-9: `recall('query', intent='preference')` returns only memories classified as preferences.
- [ ] AC-10: `recall('query', domain='technical', emotion='frustrated')` filters by both domain AND emotion.
- [ ] AC-11: `recall('query')` without classification filters returns all results (backward compatible).
- [ ] AC-12: Memories without classification data are excluded when classification filters are applied.

### MCP Tool: classify

- [ ] AC-13: `classify` tool is registered and discoverable via MCP.
- [ ] AC-14: Tool returns formatted classification with labels and confidence percentages.
- [ ] AC-15: Tool works without classification being enabled on `remember()`.

### CLI: lore classify

- [ ] AC-16: `lore classify 'text'` prints formatted classification to stdout.
- [ ] AC-17: `lore classify 'text' --json` prints JSON classification.
- [ ] AC-18: Works with rule-based fallback when no LLM is configured.

### LLM Provider (shared)

- [ ] AC-19: `LLMProvider` abstraction is importable from `lore.llm` and reusable by F6.
- [ ] AC-20: Provider configured via `LORE_LLM_PROVIDER`, `LORE_LLM_MODEL`, `LORE_LLM_API_KEY` env vars.
- [ ] AC-21: No LLM calls made when classification is disabled.

### Tests

- [ ] AC-22: Unit tests for `LLMClassifier` with mocked LLM responses (valid, malformed, error).
- [ ] AC-23: Unit tests for `RuleBasedClassifier` covering all intent/domain/emotion patterns.
- [ ] AC-24: Integration test: `remember()` with classification → `recall()` with filters → correct results.
- [ ] AC-25: Test that classification does not block `remember()` on LLM failure.

## 11. Success Metrics

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Classification accuracy (LLM) | 85%+ on intent, 80%+ on domain, 75%+ on emotion | Manual evaluation on 50 sample texts |
| Classification accuracy (rules) | 60%+ on intent, 50%+ on domain | Same sample set with rule-based classifier |
| Recall filter correctness | 100% — filtered results match classification | Integration tests |
| Latency overhead (LLM) | < 500ms per classification | Benchmark with gpt-4o-mini |
| Latency overhead (rules) | < 5ms per classification | Benchmark rule-based classifier |
| No new required dependencies | 0 — LLM provider uses httpx (already available) or stdlib | Verify in pyproject.toml |
| Test count | 30-40 new tests | pytest count |

## 12. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| LLM classification adds latency to remember() | Medium — 200-500ms per call | Use cheap/fast model (gpt-4o-mini). Classification is opt-in. Can be made async in future. |
| LLM returns invalid/unexpected labels | Medium — bad metadata | Validate against taxonomy. Fall back to rule-based on invalid response. |
| Rule-based classifier is too inaccurate | Low — only used as fallback | Set low confidence (0.3-0.6) on rule-based results. Users know it's approximate. |
| F6 and F9 create conflicting LLM provider abstractions | Medium — merge conflict | Coordinate: whichever lands first creates `src/lore/llm/`. Document the interface clearly. |
| Classification filters exclude useful memories | Low — only classified memories match | Document behavior. Unclassified memories always appear in unfiltered recall. |
| LLM API costs | Low — classification is a short prompt | Use cheap models. Single short prompt per memory. Estimate: ~$0.001 per classification with gpt-4o-mini. |

## 13. Out of Scope

- **Reclassification / batch backfill** — Future F6 enrichment pipeline concern.
- **Classification-based auto-tiering** — e.g., auto-setting `tier=working` for questions. Interesting but separate feature.
- **Multi-label classification** — Each axis gets one label. "Is this both technical AND business?" — pick the dominant one.
- **Sentiment analysis beyond emotion** — No sentiment scoring (positive/negative float). Just categorical emotion labels.
- **Classification history** — No tracking of classification changes over time.
- **User-facing classification editing** — No MCP tool to reclassify a stored memory.

## 14. Dependencies

- **Upstream:** None strictly required. Shares LLM provider with F6 (co-dependency, not sequential dependency).
- **Downstream:** F2 (Fact Extraction) may use classification to prioritize what to extract (e.g., only extract facts from `statement` and `decision` intents). F3 (Consolidation) may group by domain when consolidating.

## 15. Implementation Notes

### 15.1 LLM Prompt Design

The classification prompt should be minimal to keep costs low and latency fast. A single prompt asking for all three axes simultaneously (not three separate calls). JSON response format ensures parseability.

### 15.2 Pipeline vs. Standalone

When running in F6's enrichment pipeline, classification is one step. The pipeline passes content through: classify → enrich → extract. Classification result is available to downstream steps.

When running standalone (F9 only, no F6), classification is a direct call from `remember()`. No pipeline overhead.

### 15.3 Validation

All classification labels are validated against the fixed taxonomy lists. Unknown labels from LLM responses are rejected and the classifier falls back to rule-based for that axis.

### 15.4 Thread Safety

The classifier is stateless — safe to call concurrently. The LLM provider handles its own connection pooling.
