# F9 — Dialog Classification: User Stories

**Feature:** F9 — Dialog Classification
**PRD:** `_bmad-output/planning-artifacts/f09-dialog-classification-prd.md`
**Architecture:** `_bmad-output/implementation-artifacts/f09-dialog-classification-architecture.md`
**Date:** 2026-03-06
**Depends on:** None (standalone). Shares LLM provider with F6 if co-present.

---

## Priority Legend

| Priority | Meaning |
|----------|---------|
| P0 | Foundation — must land first |
| P1 | Core logic — depends on P0 |
| P2 | Integration — depends on P1 |
| P3 | Polish — depends on P2 |

---

## S1: Shared LLM Provider Module

**Priority:** P0 | **Estimate:** M | **Dependencies:** None

### Description

Create `src/lore/llm/` package with the abstract `LLMProvider` base class and a concrete `OpenAIProvider` implementation. This module is shared infrastructure for both F9 (classification) and F6 (metadata enrichment). Configuration via `LORE_LLM_PROVIDER`, `LORE_LLM_MODEL`, `LORE_LLM_API_KEY`, and optional `LORE_LLM_BASE_URL` env vars. Include a `create_llm_provider()` factory function.

### Files Changed

- `src/lore/llm/__init__.py` — **New.** Exports `LLMProvider`, `OpenAIProvider`, `create_llm_provider`
- `src/lore/llm/base.py` — **New.** `LLMProvider` ABC with `async complete(prompt, max_tokens) -> str`
- `src/lore/llm/openai.py` — **New.** `OpenAIProvider` using `httpx` for OpenAI-compatible APIs
- `src/lore/llm/factory.py` — **New.** `create_llm_provider(provider, model, api_key, base_url)` factory

### Acceptance Criteria

```
Given LORE_LLM_PROVIDER="openai", LORE_LLM_MODEL="gpt-4o-mini", LORE_LLM_API_KEY set
When create_llm_provider() is called
Then an OpenAIProvider instance is returned configured with those values

Given an OpenAIProvider with valid credentials
When complete(prompt="Hello", max_tokens=50) is called
Then it sends a POST to the chat completions endpoint and returns the response text

Given LORE_LLM_BASE_URL is set to a custom endpoint
When OpenAIProvider is created
Then it uses that base URL instead of the default OpenAI endpoint

Given no LORE_LLM_PROVIDER is set (or set to None)
When create_llm_provider() is called
Then it returns None (no provider available)

Given an unknown LORE_LLM_PROVIDER value (e.g., "gemini")
When create_llm_provider() is called
Then it raises a ValueError with a clear message listing supported providers
```

---

## S2: Classification Schemas, Taxonomies, and Enums

**Priority:** P0 | **Estimate:** S | **Dependencies:** None

### Description

Create `src/lore/classify/` package with taxonomy constants (intent, domain, emotion labels as tuples), the `Classification` dataclass (intent, domain, emotion, confidence dict), and the abstract `Classifier` base class. Include validation helpers to check labels against taxonomies.

### Files Changed

- `src/lore/classify/__init__.py` — **New.** Exports `Classifier`, `Classification`, taxonomy constants
- `src/lore/classify/taxonomies.py` — **New.** `INTENT_LABELS`, `DOMAIN_LABELS`, `EMOTION_LABELS` tuples with docstring definitions
- `src/lore/classify/base.py` — **New.** `Classification` dataclass, `Classifier` ABC with `classify(text) -> Classification`

### Acceptance Criteria

```
Given INTENT_LABELS is imported
When inspected
Then it contains exactly: question, statement, instruction, preference, observation, decision

Given DOMAIN_LABELS is imported
When inspected
Then it contains exactly: technical, personal, business, creative, administrative

Given EMOTION_LABELS is imported
When inspected
Then it contains exactly: neutral, frustrated, excited, curious, confident, uncertain

Given a Classification is created with intent="preference", domain="technical", emotion="confident"
When confidence={"intent": 0.9, "domain": 0.85, "emotion": 0.7} is provided
Then all fields are accessible and confidence values are floats between 0.0 and 1.0

Given Classification.to_dict() is called
When the result is inspected
Then it returns the exact structure expected for metadata.classification storage
```

---

## S3: Rule-Based Classifier

**Priority:** P1 | **Estimate:** M | **Dependencies:** S2

### Description

Implement `RuleBasedClassifier` in `src/lore/classify/rules.py`. Uses regex keyword/pattern matching for all three axes (intent, domain, emotion). Pattern-matched labels get confidence 0.6; fallback defaults (statement, personal, neutral) get confidence 0.3. This classifier is used when no LLM is configured and as fallback when LLM responses are invalid.

### Files Changed

- `src/lore/classify/rules.py` — **New.** `RuleBasedClassifier(Classifier)` with pattern dicts for all three axes
- `src/lore/classify/__init__.py` — Add `RuleBasedClassifier` to exports

### Acceptance Criteria

```
Given text "How do I deploy to staging?"
When RuleBasedClassifier.classify() is called
Then intent="question" with confidence 0.6 (matches ? pattern)

Given text "Always use bun instead of npm"
When RuleBasedClassifier.classify() is called
Then intent="instruction" or "preference" with confidence 0.6 (matches keyword patterns)

Given text "The deploy took 12 minutes today"
When RuleBasedClassifier.classify() is called
Then domain="technical" with confidence 0.6 (matches "deploy" keyword)

Given text "This keeps breaking every time"
When RuleBasedClassifier.classify() is called
Then emotion="frustrated" with confidence 0.6 (matches "keeps breaking" pattern)

Given text with no matching patterns for an axis
When RuleBasedClassifier.classify() is called
Then the fallback default is used (statement/personal/neutral) with confidence 0.3

Given any text input (including empty string)
When RuleBasedClassifier.classify() is called
Then a valid Classification is always returned (never raises, never returns None)
```

---

## S4: LLM Classifier with Rule-Based Fallback

**Priority:** P1 | **Estimate:** M | **Dependencies:** S1, S3

### Description

Implement `LLMClassifier` in `src/lore/classify/llm.py`. Sends a single prompt to the LLM requesting JSON classification across all three axes. Validates the response against taxonomies. Falls back to `RuleBasedClassifier` on LLM error, malformed JSON, or invalid labels (per-axis: if one axis has an invalid label, only that axis falls back to rule-based).

### Files Changed

- `src/lore/classify/llm.py` — **New.** `LLMClassifier(Classifier)` wrapping `LLMProvider` with `RuleBasedClassifier` fallback
- `src/lore/classify/__init__.py` — Add `LLMClassifier` to exports

### Acceptance Criteria

```
Given an LLMClassifier with a valid LLMProvider
When classify("I always use bun") is called and the LLM returns valid JSON
Then a Classification is returned with LLM-provided labels and confidence scores

Given an LLMClassifier
When the LLM returns malformed JSON (not parseable)
Then the full response falls back to RuleBasedClassifier
And no exception is raised

Given an LLMClassifier
When the LLM returns valid JSON but intent="unknown" (not in taxonomy)
Then intent falls back to rule-based for that axis only
And domain and emotion use the LLM values if valid

Given an LLMClassifier
When the LLM call raises an exception (network error, timeout, etc.)
Then the full response falls back to RuleBasedClassifier
And no exception is raised

Given an LLMClassifier
When the LLM returns confidence values outside 0.0-1.0
Then they are clamped to [0.0, 1.0]
```

---

## S5: Integration into remember() — Classification on Store

**Priority:** P2 | **Estimate:** M | **Dependencies:** S4

### Description

Wire classification into `Lore.__init__()` and `Lore.remember()`. Add `classify: bool = False` constructor param (also from `LORE_CLASSIFY` env var). When enabled, classify content before storing and write result to `metadata.classification`. Classification failure must never prevent memory storage. Add `Lore.classify(text)` public method for standalone use.

### Files Changed

- `src/lore/lore.py` — Add `classify` param to `__init__`, instantiate classifier (LLM or rule-based), add classification step in `remember()`, add `classify()` method
- `src/lore/lore.py` — Add `classification_confidence_threshold` param; mark low-confidence results with `low_confidence: true`

### Acceptance Criteria

```
Given Lore(classify=True) with no LLM configured
When remember("I prefer vim over emacs") is called
Then the stored memory has metadata.classification with rule-based labels
And the memory is stored successfully

Given Lore(classify=True) with LLM configured
When remember("Deploy the service to prod") is called
Then the stored memory has metadata.classification with LLM-provided labels

Given Lore(classify=False) (default)
When remember("any content") is called
Then no classification step runs and metadata has no classification key

Given Lore(classify=True) and the classifier raises an unexpected error
When remember("some text") is called
Then the memory is stored without classification (graceful degradation)
And a warning is logged

Given Lore(classify=True, classification_confidence_threshold=0.5)
When a classification has min confidence below 0.5
Then metadata.classification includes "low_confidence": true

Given lore.classify("Why does this break?") called directly
When classification is not enabled on remember()
Then it still returns a valid Classification (standalone usage works)
```

---

## S6: Recall Filtering by Classification

**Priority:** P2 | **Estimate:** M | **Dependencies:** S5

### Description

Add optional `intent`, `domain`, and `emotion` filter parameters to `Lore.recall()`. Apply post-filtering after vector similarity search. Memories without classification data are excluded when any classification filter is applied. Also add the same filters to `list_memories()`. Update MCP `recall` tool output to include classification labels when present.

### Files Changed

- `src/lore/lore.py` — Add `intent`, `domain`, `emotion` optional params to `recall()`, add `_matches_classification()` helper, add same params to `list_memories()`
- `src/lore/mcp/server.py` — Add `intent`, `domain`, `emotion` params to MCP `recall` tool; include classification labels in recall output formatting

### Acceptance Criteria

```
Given memories stored with classification (some technical preferences, some personal observations)
When recall("tools", intent="preference") is called
Then only memories classified as intent=preference are returned

Given memories with mixed classifications
When recall("query", domain="technical", emotion="frustrated") is called
Then only memories matching BOTH domain=technical AND emotion=frustrated are returned

Given recall("query") with no classification filters
When called
Then all matching memories are returned (backward compatible, no filtering)

Given a memory stored without classification (classify was disabled)
When recall("query", intent="preference") is called
Then that unclassified memory is excluded from results

Given list_memories(intent="question") is called
When there are classified memories
Then only memories with intent=question are returned

Given MCP recall tool returns results with classification
When the output is formatted
Then classification labels appear (e.g., "[preference, technical, confident]")
```

---

## S7: Standalone classify MCP Tool and CLI Command

**Priority:** P2 | **Estimate:** S | **Dependencies:** S4

### Description

Add MCP `classify` tool that classifies arbitrary text without storing. Add `lore classify` CLI subcommand with formatted output and `--json` flag. Both work regardless of whether classification is enabled on `remember()` and use LLM if configured, otherwise rule-based fallback.

### Files Changed

- `src/lore/mcp/server.py` — Add `classify` tool (text -> formatted string)
- `src/lore/cli.py` — Add `classify` subcommand with `--json` and `--provider` flags

### Acceptance Criteria

```
Given the MCP classify tool is called with text="I always use bun"
When executed
Then it returns formatted output like "Intent: preference (92%)\nDomain: technical (95%)\nEmotion: confident (78%)"

Given the MCP classify tool
When discovered via MCP tool listing
Then it appears with description explaining its purpose

Given CLI command "lore classify 'Why does this keep breaking?'"
When executed with no LLM configured
Then it prints formatted classification using rule-based fallback

Given CLI command "lore classify 'text' --json"
When executed
Then it prints the classification as a JSON object to stdout

Given no LLM configured
When either MCP classify or CLI classify is used
Then rule-based classifier is used and results are returned (no error)
```

---

## S8: Tests — Classifiers, Fallback Logic, Integration, Edge Cases

**Priority:** P3 | **Estimate:** L | **Dependencies:** S5, S6, S7

### Description

Comprehensive test suite covering: unit tests for `RuleBasedClassifier` (all pattern categories), unit tests for `LLMClassifier` (mocked LLM — valid, malformed, error responses), integration tests (remember with classify -> recall with filters), edge cases (empty strings, very long text, unicode), LLM provider tests (mocked HTTP), and CLI/MCP tool tests.

### Files Changed

- `tests/test_classification.py` — **New.** Unit tests for `Classification` dataclass, taxonomy validation, `LLMClassifier` with mocked provider
- `tests/test_classification_rules.py` — **New.** Exhaustive tests for `RuleBasedClassifier` — every pattern category, fallback defaults, edge cases
- `tests/test_classification_integration.py` — **New.** End-to-end: remember() with classification -> recall() with filters -> correct results
- `tests/test_llm_provider.py` — **New.** Tests for `LLMProvider` factory, `OpenAIProvider` with mocked HTTP

### Acceptance Criteria

```
Given RuleBasedClassifier
When tested with representative texts for each intent label
Then each text is classified to the expected intent

Given RuleBasedClassifier
When tested with representative texts for each domain label
Then each text is classified to the expected domain

Given RuleBasedClassifier
When tested with representative texts for each emotion label
Then each text is classified to the expected emotion

Given LLMClassifier with a mock provider returning valid JSON
When classify() is called
Then correct Classification object is returned with LLM values

Given LLMClassifier with a mock provider returning malformed JSON
When classify() is called
Then RuleBasedClassifier fallback is used (no exception)

Given LLMClassifier with a mock provider that raises an exception
When classify() is called
Then RuleBasedClassifier fallback is used (no exception)

Given Lore(classify=True) with mocked classifier
When remember("I prefer dark mode") then recall("dark mode", intent="preference")
Then the stored memory is returned with correct classification

Given Lore(classify=True) with mocked classifier
When remember("test") then recall("test", intent="question")
Then the non-matching memory is NOT returned

Given empty string or whitespace-only input
When any classifier.classify() is called
Then a valid Classification with fallback defaults is returned (no crash)

Given test coverage
When measured
Then at least 30 new tests exist across the test files
```

---

## Dependency Graph

```
S1 (LLM Provider)     S2 (Schemas/Taxonomies)
        \                  /       \
         \                /         \
          S4 (LLM Classifier)   S3 (Rule-Based Classifier)
               |                   /
               |                  /
          S5 (remember integration)
           /          \
          /            \
S6 (recall filtering)  S7 (MCP tool + CLI)
          \            /
           \          /
        S8 (Tests — all)
```

## Story Summary

| Story | Title | Est. | Priority | Dependencies |
|-------|-------|------|----------|-------------|
| S1 | Shared LLM Provider Module | M | P0 | None |
| S2 | Classification Schemas & Taxonomies | S | P0 | None |
| S3 | Rule-Based Classifier | M | P1 | S2 |
| S4 | LLM Classifier with Fallback | M | P1 | S1, S3 |
| S5 | Integration into remember() | M | P2 | S4 |
| S6 | Recall Filtering by Classification | M | P2 | S5 |
| S7 | Standalone classify Tool + CLI | S | P2 | S4 |
| S8 | Tests — Full Suite | L | P3 | S5, S6, S7 |
