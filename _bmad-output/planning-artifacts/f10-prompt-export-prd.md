# PRD: F10 — Memory Export / Prompt Formatting (`as_prompt`)

**Feature:** F10
**Version:** v0.6.0 ("Open Brain")
**Status:** Draft
**Author:** John (PM)
**Phase:** 1 — Foundation Layer

---

## 1. Problem Statement

LLM agents recall memories from Lore, but the raw output is formatted for humans (plain text with separators). When agents want to inject memories into their context window as grounding knowledge, they need:

- **Structured formatting** that matches their LLM's preferred input format (Claude expects XML tags, ChatML uses `<|im_start|>` markers, others want markdown or raw text)
- **Context budget control** — agents operate under token limits and need to specify how much context they can afford
- **Optimized ordering** — highest-relevance memories first, with smart truncation that preserves the most valuable content

Today, agents must manually parse `recall()` output and reformat it. This is fragile, wastes tokens on formatting logic, and produces suboptimal context injection.

## 2. Solution Overview

Add an `as_prompt()` method to the `Lore` class and expose it as both an MCP tool and CLI command. This method:

1. Calls `recall()` internally to retrieve relevant memories
2. Formats results using a configurable template system
3. Respects a context budget (max characters/tokens) with smart truncation
4. Returns a single string ready for direct injection into an LLM prompt

## 3. User Stories

**US-1:** As an AI agent, I want to retrieve formatted memories optimized for my LLM's context window so I can inject relevant knowledge without manual parsing.

**US-2:** As a developer building an agent, I want to call `lore.as_prompt("deployment", format="xml", max_tokens=2000)` and get back a ready-to-use XML block of relevant memories.

**US-3:** As a CLI user, I want to run `lore prompt 'query' --format markdown --max-tokens 1500` and pipe the output into another tool.

**US-4:** As an MCP tool consumer, I want an `as_prompt` tool that returns formatted memories I can embed directly in my system prompt.

## 4. Detailed Requirements

### 4.1 `Lore.as_prompt()` Method

```python
def as_prompt(
    self,
    query: str,
    *,
    format: str = "xml",           # "xml" | "chatml" | "markdown" | "raw"
    max_tokens: Optional[int] = None,  # context budget (approximate)
    max_chars: Optional[int] = None,   # alternative: char-based budget
    limit: int = 10,               # max memories to consider (passed to recall)
    tags: Optional[List[str]] = None,
    type: Optional[str] = None,
    min_score: float = 0.0,        # filter out low-relevance results
    include_metadata: bool = False, # include type, tags, score in output
    project: Optional[str] = None,
) -> str:
```

**Behavior:**

1. Call `self.recall(query, tags=tags, type=type, limit=limit)` to get `RecallResult` list
2. Filter results below `min_score`
3. Format each memory using the selected template
4. Accumulate formatted memories until context budget is reached
5. Return the complete formatted string (or empty string if no results)

**Token estimation:** Use a simple heuristic — `len(text) / 4` for approximate token count. This avoids adding a tokenizer dependency. The heuristic is sufficient because the budget is advisory, not exact.

**Budget enforcement:** Memories are added in score-descending order. When adding the next memory would exceed the budget, stop. Do not partially truncate a memory — include it whole or skip it. If even the first memory exceeds the budget, include it anyway (at least one result is always returned if any match).

### 4.2 Template Formats

#### `xml` (default) — optimized for Claude

```xml
<memories query="deployment issues">
<memory type="lesson" score="0.87">
Always use rolling deployments for zero-downtime releases.
</memory>
<memory type="fact" score="0.72">
Production uses k8s namespace "prod-main" on cluster us-east-1.
</memory>
</memories>
```

When `include_metadata=True`, add attributes:

```xml
<memory type="lesson" score="0.87" tags="devops,k8s" id="01HX..." created="2025-03-01">
```

#### `markdown`

```markdown
## Relevant Memories: deployment issues

- **[lesson, 0.87]** Always use rolling deployments for zero-downtime releases.
- **[fact, 0.72]** Production uses k8s namespace "prod-main" on cluster us-east-1.
```

#### `chatml` — for OpenAI-style models

```
<|im_start|>system
Relevant memories for: deployment issues

[lesson, 0.87] Always use rolling deployments for zero-downtime releases.
[fact, 0.72] Production uses k8s namespace "prod-main" on cluster us-east-1.
<|im_end|>
```

#### `raw` — plain text, no markup

```
Relevant memories for: deployment issues

Always use rolling deployments for zero-downtime releases.

Production uses k8s namespace "prod-main" on cluster us-east-1.
```

### 4.3 MCP Tool: `as_prompt`

```python
@mcp.tool(
    description=(
        "Export memories formatted for LLM context injection. "
        "USE THIS WHEN: you need to inject relevant memories directly into a prompt "
        "or system message. Returns a formatted block of memories optimized for your "
        "LLM's preferred format. Supports XML (Claude), ChatML (OpenAI), markdown, "
        "and raw text."
    ),
)
def as_prompt(
    query: str,
    format: str = "xml",
    max_tokens: Optional[int] = None,
    limit: int = 10,
    tags: Optional[List[str]] = None,
    type: Optional[str] = None,
    include_metadata: bool = False,
) -> str:
```

**Returns:** The formatted string directly (not wrapped in a status message).

### 4.4 CLI Command: `lore prompt`

```
lore prompt 'deployment issues' --format xml --max-tokens 2000
lore prompt 'testing patterns' --format markdown --limit 5
lore prompt 'user preferences' --format raw --include-metadata
```

**Arguments:**
- `query` (positional): search query
- `--format`: xml, chatml, markdown, raw (default: xml)
- `--max-tokens`: context budget in approximate tokens
- `--max-chars`: context budget in characters
- `--limit`: max memories to retrieve (default: 10)
- `--type`: filter by memory type
- `--tags`: comma-separated tag filter
- `--min-score`: minimum relevance score (default: 0.0)
- `--include-metadata`: include type, tags, score, id in output

**Output:** Prints the formatted string to stdout (no extra decoration). This makes it pipe-friendly.

## 5. Architecture & Implementation Notes

### 5.1 File Layout

```
src/lore/
├── lore.py              # Add as_prompt() method
├── prompt/
│   ├── __init__.py
│   ├── formatter.py     # PromptFormatter class with format_memories()
│   └── templates.py     # Template strings/functions per format
├── mcp/
│   └── server.py        # Add as_prompt tool
└── cli.py               # Add prompt subcommand
```

### 5.2 Design Decisions

- **Formatter is a standalone class** — `PromptFormatter` takes `List[RecallResult]` and format options, returns a string. This keeps it testable independently of `Lore`.
- **No Jinja2 dependency** — templates are simple Python string formatting. The formats are fixed and well-defined; a template engine adds complexity for no benefit.
- **Token estimation is intentionally approximate** — `len(text) / 4` is the industry-standard rough heuristic. Adding tiktoken or similar would create a heavy dependency for marginal accuracy. Document this as approximate.
- **`as_prompt()` delegates to `recall()`** — no separate retrieval logic. This ensures consistent scoring, decay, freshness, and filtering behavior.

### 5.3 Integration with Existing Code

- `as_prompt()` on `Lore` class calls `self.recall()` then passes results to `PromptFormatter.format()`
- The MCP tool calls `_get_lore().as_prompt()`
- The CLI command creates a `Lore` instance and calls `as_prompt()`, prints to stdout

## 6. Out of Scope

- Custom user-defined templates (future consideration — for now, 4 fixed formats)
- Exact token counting via tokenizer libraries
- Streaming output
- Memory grouping/clustering within the prompt (future: could group by type or topic)
- Integration with specific LLM APIs (this just produces text — the caller decides where to inject it)

## 7. Acceptance Criteria

### SDK (`Lore.as_prompt()`)

- [ ] AC-1: `as_prompt(query)` returns XML-formatted memories by default
- [ ] AC-2: `format` parameter accepts "xml", "chatml", "markdown", "raw" — raises `ValueError` for unknown formats
- [ ] AC-3: `max_tokens` limits output to approximately N tokens (within 20% margin)
- [ ] AC-4: `max_chars` limits output to N characters
- [ ] AC-5: When both `max_tokens` and `max_chars` are provided, the stricter limit wins
- [ ] AC-6: Memories appear in score-descending order (highest relevance first)
- [ ] AC-7: `min_score` filters out results below the threshold
- [ ] AC-8: Returns empty string when no memories match the query
- [ ] AC-9: At least one memory is always included if any match, even if it exceeds budget
- [ ] AC-10: `include_metadata=True` adds type, tags, score, id to each memory's output
- [ ] AC-11: All `recall()` parameters (tags, type, limit) pass through correctly
- [ ] AC-12: XML output is well-formed (parseable by an XML parser)

### MCP Tool

- [ ] AC-13: `as_prompt` tool is registered and discoverable via MCP
- [ ] AC-14: Tool returns the formatted string directly (not wrapped in status text)
- [ ] AC-15: Tool handles errors gracefully with descriptive messages

### CLI

- [ ] AC-16: `lore prompt 'query'` works with default settings
- [ ] AC-17: All CLI flags (`--format`, `--max-tokens`, `--max-chars`, `--limit`, `--type`, `--tags`, `--min-score`, `--include-metadata`) work correctly
- [ ] AC-18: Output goes to stdout with no extra decoration (pipe-friendly)

### Tests

- [ ] AC-19: Unit tests for each format template (xml, chatml, markdown, raw)
- [ ] AC-20: Unit tests for budget enforcement (max_tokens, max_chars, both, neither)
- [ ] AC-21: Unit tests for edge cases: no results, single result exceeding budget, min_score filtering
- [ ] AC-22: Integration test: `as_prompt()` end-to-end with in-memory store
- [ ] AC-23: MCP tool test matching existing test patterns in the codebase

## 8. Success Metrics

| Metric | Target |
|--------|--------|
| All acceptance criteria passing | 100% |
| New test count | 15-25 tests |
| No new dependencies added | 0 new packages |
| `as_prompt()` latency vs `recall()` | < 5ms overhead (formatting only) |
| Output token accuracy | Within 20% of `max_tokens` budget |

## 9. Dependencies

- **Upstream:** None — this is the first feature in Phase 1 with zero dependencies
- **Downstream:** Future features (F3 consolidation summaries, F8 cross-tool docs) may reference `as_prompt()` as the canonical way to export memories for LLM consumption

## 10. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Token estimation inaccuracy | Low — budget is advisory | Document as approximate; users can use `max_chars` for precision |
| XML special characters in memory content | Medium — malformed output | Escape `<`, `>`, `&` in memory content within XML templates |
| Large memory content blowing budget | Low — one memory fills budget | AC-9 guarantees at least one result; truncation is explicit |

## 11. Implementation Estimate

**Size:** Small (S)
**Complexity:** Low — straightforward string formatting over existing `recall()` results
**Files touched:** 4-5 (new `prompt/` module, edits to `lore.py`, `server.py`, `cli.py`)
