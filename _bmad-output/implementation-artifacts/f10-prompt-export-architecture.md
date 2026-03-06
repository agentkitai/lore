# F10 — Memory Export / Prompt Formatting: Architecture

**Feature:** F10 — `as_prompt()`
**Version:** v0.6.0 ("Open Brain")
**Status:** Ready for Implementation
**PRD:** `_bmad-output/planning-artifacts/f10-prompt-export-prd.md`

---

## 1. Module Structure

```
src/lore/
├── lore.py                  # Add as_prompt() method
├── prompt/
│   ├── __init__.py          # Re-export PromptFormatter, FORMAT_REGISTRY
│   ├── formatter.py         # PromptFormatter class + budget logic
│   └── templates.py         # Format functions: xml, chatml, markdown, raw
├── mcp/
│   └── server.py            # Add as_prompt tool
└── cli.py                   # Add prompt subcommand

tests/
├── test_prompt_formatter.py # Unit tests for formatter + templates
└── test_prompt_mcp.py       # MCP tool integration test (or add to test_mcp.py)
```

**Decision: No ABC hierarchy for formatters.** The PRD defines exactly 4 fixed formats with no extensibility requirement. An abstract `PromptFormatter` base with `XMLFormatter`, `ChatMLFormatter`, etc. subclasses is overengineered for what amounts to 4 string-formatting functions. Instead, use a single `PromptFormatter` class with a registry of format functions.

This follows the existing Lore pattern: `Store` uses an ABC because backends have fundamentally different implementations (SQLite vs HTTP vs memory). Format templates are just string operations — a function registry is the right abstraction level.

---

## 2. Core Design

### 2.1 `templates.py` — Format Functions

Each format is a plain function with this signature:

```python
from typing import List
from lore.types import RecallResult

FormatFn = Callable[[str, List[RecallResult], bool], str]
#                    query, results, include_metadata -> formatted_string

def format_xml(query: str, results: List[RecallResult], include_metadata: bool) -> str:
    ...

def format_markdown(query: str, results: List[RecallResult], include_metadata: bool) -> str:
    ...

def format_chatml(query: str, results: List[RecallResult], include_metadata: bool) -> str:
    ...

def format_raw(query: str, results: List[RecallResult], include_metadata: bool) -> str:
    ...

FORMAT_REGISTRY: Dict[str, FormatFn] = {
    "xml": format_xml,
    "markdown": format_markdown,
    "chatml": format_chatml,
    "raw": format_raw,
}
```

**XML escaping:** `format_xml` must escape `<`, `>`, `&`, `"` in memory content and attribute values using `xml.sax.saxutils.escape` (stdlib). This is the only format that needs escaping.

**Each function formats ALL provided results.** Budget enforcement happens upstream in the formatter — templates receive pre-trimmed result lists.

### 2.2 `formatter.py` — PromptFormatter

```python
from typing import List, Optional
from lore.types import RecallResult
from lore.prompt.templates import FORMAT_REGISTRY, FormatFn

class PromptFormatter:
    """Formats RecallResult lists into LLM-ready prompt strings."""

    def format(
        self,
        query: str,
        results: List[RecallResult],
        *,
        format: str = "xml",
        max_tokens: Optional[int] = None,
        max_chars: Optional[int] = None,
        min_score: float = 0.0,
        include_metadata: bool = False,
    ) -> str:
        """Format recall results into a prompt string.

        1. Filter by min_score
        2. Enforce budget (max_tokens/max_chars) by including memories
           in score-descending order until budget is exhausted
        3. Format using the selected template
        """
```

**Budget enforcement algorithm:**

```
effective_budget = compute_effective_budget(max_tokens, max_chars)
# If both set: min(max_tokens * 4, max_chars) — stricter wins

included = []
running_chars = 0
for result in filtered_results:  # already sorted by score desc from recall()
    # Estimate char cost of this memory's formatted output
    entry_chars = estimate_entry_chars(result, format, include_metadata)
    if included and (running_chars + entry_chars) > effective_budget:
        break  # skip rest — budget exhausted
    included.append(result)
    running_chars += entry_chars

return FORMAT_REGISTRY[format](query, included, include_metadata)
```

**Key decisions:**
- **Budget is checked in character space.** `max_tokens` is converted to chars via `max_tokens * 4`. This avoids dual-tracking.
- **Entry cost estimation uses the raw content length + a per-format overhead constant** (XML tags ~50 chars, markdown bullets ~20 chars, etc.). This is cheaper than formatting each entry then measuring — and accurate enough since the budget is advisory.
- **At least one result is always included** if any pass the `min_score` filter (`if included and ...` — the first item always passes).
- **Results arrive pre-sorted** from `recall()` (score descending). No re-sorting needed.

### 2.3 `lore.py` — `as_prompt()` method

```python
def as_prompt(
    self,
    query: str,
    *,
    format: str = "xml",
    max_tokens: Optional[int] = None,
    max_chars: Optional[int] = None,
    limit: int = 10,
    tags: Optional[List[str]] = None,
    type: Optional[str] = None,
    min_score: float = 0.0,
    include_metadata: bool = False,
    project: Optional[str] = None,
) -> str:
    results = self.recall(
        query, tags=tags, type=type, limit=limit
    )
    formatter = PromptFormatter()
    return formatter.format(
        query, results,
        format=format,
        max_tokens=max_tokens,
        max_chars=max_chars,
        min_score=min_score,
        include_metadata=include_metadata,
    )
```

**Note:** `PromptFormatter()` is stateless and cheap to construct. No need to cache it on the `Lore` instance — it has no initialization cost.

---

## 3. Data Flow

```
User/Agent
    │
    ├─ SDK: lore.as_prompt("deployment", format="xml", max_tokens=2000)
    ├─ MCP: as_prompt tool call
    └─ CLI: lore prompt 'deployment' --format xml --max-tokens 2000
    │
    ▼
Lore.as_prompt()
    │
    ├── 1. self.recall(query, tags=..., type=..., limit=...)
    │       → List[RecallResult] (scored, sorted desc)
    │
    ├── 2. PromptFormatter.format(query, results, ...)
    │       ├── Filter by min_score
    │       ├── Compute effective char budget
    │       ├── Accumulate results until budget hit
    │       └── Call FORMAT_REGISTRY[format](query, included, include_metadata)
    │           → str
    │
    └── 3. Return formatted string
```

---

## 4. Integration Points

### 4.1 MCP Tool (`mcp/server.py`)

Add after existing tools, following the established pattern:

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
    try:
        lore = _get_lore()
        return lore.as_prompt(
            query, format=format, max_tokens=max_tokens,
            limit=limit, tags=tags, type=type,
            include_metadata=include_metadata,
        )
    except Exception as e:
        return f"Failed to format memories: {e}"
```

**Note:** The MCP tool returns the formatted string directly — no wrapping in status messages. This is a deliberate departure from `recall()` which wraps in "Found N memory(ies):" text. The `as_prompt` output is meant for direct injection, not human reading.

**Note:** `max_chars` is intentionally omitted from MCP — agents think in tokens, not chars. CLI supports both.

### 4.2 CLI Command (`cli.py`)

Add `prompt` subcommand to `build_parser()` and a `cmd_prompt()` handler:

```python
# In build_parser():
p = sub.add_parser("prompt", help="Export memories formatted for LLM prompts")
p.add_argument("query", help="Search query")
p.add_argument("--format", default="xml", choices=["xml", "chatml", "markdown", "raw"])
p.add_argument("--max-tokens", type=int, default=None)
p.add_argument("--max-chars", type=int, default=None)
p.add_argument("--limit", type=int, default=10)
p.add_argument("--type", default=None)
p.add_argument("--tags", default=None, help="Comma-separated tags")
p.add_argument("--min-score", type=float, default=0.0)
p.add_argument("--include-metadata", action="store_true", default=False)

# In handlers dict:
"prompt": cmd_prompt,
```

`cmd_prompt()` prints result to stdout with no decoration — pipe-friendly.

### 4.3 Public API (`__init__.py`)

No changes needed. `as_prompt()` is a method on `Lore`, which is already exported. No new public types are introduced.

---

## 5. API Contract

### `Lore.as_prompt()`

```python
def as_prompt(
    self,
    query: str,
    *,
    format: str = "xml",           # "xml" | "chatml" | "markdown" | "raw"
    max_tokens: Optional[int] = None,
    max_chars: Optional[int] = None,
    limit: int = 10,
    tags: Optional[List[str]] = None,
    type: Optional[str] = None,
    min_score: float = 0.0,
    include_metadata: bool = False,
    project: Optional[str] = None,  # override instance project
) -> str:
```

**Return value:**
- Formatted string ready for prompt injection
- Empty string `""` if no memories match (after min_score filtering)

**Raises:**
- `ValueError` if `format` is not one of the 4 valid formats
- Any exception from `recall()` propagates (embedding errors, store errors)

**Guarantees:**
- Results are in score-descending order
- At least one memory included if any match, even if it exceeds budget
- XML output is well-formed and parseable

### `PromptFormatter.format()`

Same parameters minus `query`-retrieval concerns (`tags`, `type`, `limit`, `project`). Takes pre-fetched `List[RecallResult]`. This is the unit-testable surface.

---

## 6. Template System

### Design: Pure Functions, No Template Engine

Templates are Python functions, not template strings. This is the right call because:
1. Each format has different structural requirements (XML needs escaping, ChatML has special tokens)
2. The formats are fixed — no user-defined templates in v0.6.0
3. Python string formatting is faster and more debuggable than any template engine

### Format Specifications

**XML (default):**
```python
def format_xml(query: str, results: List[RecallResult], include_metadata: bool) -> str:
    # Uses xml.sax.saxutils.escape() for content
    # Uses xml.sax.saxutils.quoteattr() for attribute values
    # Outputs well-formed XML parseable by xml.etree.ElementTree
```

Output shape:
```xml
<memories query="...">
<memory type="..." score="0.87">escaped content</memory>
</memories>
```

With `include_metadata=True`, adds attributes: `tags`, `id`, `created`.

**Markdown:**
```python
def format_markdown(query: str, results: List[RecallResult], include_metadata: bool) -> str:
```

Output shape:
```markdown
## Relevant Memories: query text

- **[type, 0.87]** Content here.
- **[type, 0.72]** More content.
```

**ChatML:**
```python
def format_chatml(query: str, results: List[RecallResult], include_metadata: bool) -> str:
```

Output shape:
```
<|im_start|>system
Relevant memories for: query text

[type, 0.87] Content here.
[type, 0.72] More content.
<|im_end|>
```

**Raw:**
```python
def format_raw(query: str, results: List[RecallResult], include_metadata: bool) -> str:
```

Output shape:
```
Relevant memories for: query text

Content here.

More content.
```

### Per-Entry Overhead Constants

Used for budget estimation without formatting:

```python
_OVERHEAD_CHARS = {
    "xml": 60,       # <memory type="..." score="0.00">\n</memory>\n
    "markdown": 25,   # - **[type, 0.00]** \n
    "chatml": 20,     # [type, 0.00] \n
    "raw": 2,         # \n\n
}

_WRAPPER_CHARS = {
    "xml": 80,        # <memories query="...">\n</memories>
    "markdown": 40,   # ## Relevant Memories: ...\n\n
    "chatml": 60,     # <|im_start|>system\n...<|im_end|>
    "raw": 30,        # Relevant memories for: ...\n\n
}
```

---

## 7. Error Handling

| Scenario | Behavior |
|----------|----------|
| Unknown format string | `ValueError("Unknown format 'foo'. Must be one of: xml, chatml, markdown, raw")` |
| No memories match query | Return `""` (empty string) |
| All results below `min_score` | Return `""` |
| First memory exceeds budget | Include it anyway (at-least-one guarantee) |
| Both `max_tokens` and `max_chars` set | Stricter limit wins: `min(max_tokens * 4, max_chars)` |
| Neither budget set | No truncation — include all results up to `limit` |
| `recall()` raises exception | Propagates from `as_prompt()` in SDK; caught and returned as error string in MCP tool |
| Memory content contains XML special chars | Escaped via `xml.sax.saxutils.escape()` — never produces malformed XML |
| Negative `max_tokens` or `max_chars` | Treated as no budget (same as `None`). No error — defensive. |

**MCP tool error pattern** follows existing convention (see `server.py:108`):
```python
except Exception as e:
    return f"Failed to format memories: {e}"
```

**CLI error pattern**: Print to stderr, exit 1 — but realistically, errors here are rare since the only validation is format name.

---

## 8. Testing Strategy

### 8.1 File: `tests/test_prompt_formatter.py`

**Unit Tests — Templates (one class per format):**

```python
class TestXMLFormat:
    def test_basic_output(self)           # AC-1: default XML format
    def test_well_formed_xml(self)        # AC-12: parseable by xml.etree
    def test_xml_escaping(self)           # content with <>&" chars
    def test_include_metadata(self)       # AC-10: tags, id, created attrs
    def test_empty_results(self)          # returns ""

class TestMarkdownFormat:
    def test_basic_output(self)
    def test_include_metadata(self)
    def test_empty_results(self)

class TestChatMLFormat:
    def test_basic_output(self)
    def test_include_metadata(self)

class TestRawFormat:
    def test_basic_output(self)
    def test_no_markup(self)              # verify no XML/markdown/ChatML markers
```

**Unit Tests — Budget Enforcement:**

```python
class TestBudgetEnforcement:
    def test_max_tokens_limits_output(self)          # AC-3
    def test_max_chars_limits_output(self)            # AC-4
    def test_both_budgets_stricter_wins(self)         # AC-5
    def test_no_budget_includes_all(self)
    def test_first_memory_always_included(self)       # AC-9
    def test_score_descending_order(self)             # AC-6
    def test_token_estimation_accuracy(self)          # within 20% margin
```

**Unit Tests — Filtering & Edge Cases:**

```python
class TestFiltering:
    def test_min_score_filters(self)                  # AC-7
    def test_min_score_zero_no_filter(self)
    def test_empty_recall_returns_empty(self)          # AC-8
    def test_unknown_format_raises(self)               # AC-2
    def test_single_large_memory_included(self)
```

### 8.2 File: `tests/test_prompt_mcp.py` (or extend `test_mcp.py`)

Following existing `test_mcp.py` patterns:

```python
class TestMCPAsPrompt:
    def test_as_prompt_tool_exists(self, mock_lore)      # AC-13
    def test_as_prompt_returns_formatted(self, mock_lore) # AC-14
    def test_as_prompt_error_handling(self, mock_lore)     # AC-15
    def test_as_prompt_empty_results(self, mock_lore)
```

### 8.3 CLI Tests (extend `test_cli.py`)

```python
class TestCLIPrompt:
    def test_prompt_default(self)           # AC-16
    def test_prompt_all_flags(self)         # AC-17
    def test_prompt_stdout_clean(self)      # AC-18: no decoration
```

### 8.4 Integration Test

```python
class TestAsPromptIntegration:
    def test_end_to_end_with_memory_store(self)  # AC-22
        # Create Lore with MemoryStore, remember 3 items,
        # call as_prompt(), verify formatted output contains memories
```

### Test Helpers

Use `MemoryStore` (in-memory) + stub embedder, same as `test_mcp.py`:

```python
def _make_results(contents: List[str], scores: Optional[List[float]] = None) -> List[RecallResult]:
    """Create RecallResult list for testing formatters directly."""
```

**Expected test count: ~20-22 tests** (within PRD target of 15-25).

---

## 9. Performance Considerations

- **No tokenizer dependency.** Token estimation is `len(text) / 4` — O(1) per memory.
- **No pre-formatting for budget checks.** Use overhead constants + content length to estimate, then format only the included set. This avoids formatting memories that get discarded.
- **Single pass.** Results are already sorted from `recall()`. Budget check is a single linear scan.
- **`PromptFormatter` is stateless.** No allocation overhead — can be instantiated per-call.
- **Streaming-friendly output.** All format functions produce complete strings, but the structure (header → entries → footer) is compatible with future streaming if needed.
- **No regex in hot path.** XML escaping uses `xml.sax.saxutils.escape()` which is C-optimized in CPython.
- **Overhead target:** < 5ms for formatting (per PRD). Given that it's pure string operations on ~10 memories, this is trivially met.

---

## 10. Backward Compatibility

**Zero breaking changes.** This feature is purely additive:

- `Lore` class gains one new method (`as_prompt`). No existing methods or signatures change.
- New `prompt/` subpackage — no existing module is moved or renamed.
- `mcp/server.py` gains one new tool — existing tools unchanged.
- `cli.py` gains one new subcommand — existing commands unchanged.
- `__init__.py` unchanged — no new public exports needed.
- `types.py` unchanged — uses existing `RecallResult` and `Memory` as-is.
- No new dependencies in `pyproject.toml`.

---

## 11. Implementation Plan

Ordered by dependency. Each step is independently testable.

| Step | Files | Description |
|------|-------|-------------|
| 1 | `src/lore/prompt/__init__.py`, `templates.py` | Create `prompt/` package. Implement 4 format functions + `FORMAT_REGISTRY`. |
| 2 | `src/lore/prompt/formatter.py` | Implement `PromptFormatter` with budget logic. |
| 3 | `tests/test_prompt_formatter.py` | Unit tests for templates + formatter + budget. |
| 4 | `src/lore/lore.py` | Add `as_prompt()` method delegating to `PromptFormatter`. |
| 5 | `src/lore/mcp/server.py` | Add `as_prompt` MCP tool. |
| 6 | `src/lore/cli.py` | Add `prompt` CLI subcommand. |
| 7 | `tests/test_prompt_mcp.py` | MCP + CLI + integration tests. |

Steps 1-2 can be implemented together. Step 3 validates them. Steps 4-6 are independent of each other (all depend on steps 1-2). Step 7 validates the integration points.

---

## 12. Open Questions (Resolved)

| Question | Resolution |
|----------|-----------|
| Should `PromptFormatter` be an ABC? | **No.** 4 fixed formats = function registry. ABCs are for when implementations have fundamentally different concerns (like `Store`). |
| Should we support custom templates? | **No.** Out of scope per PRD section 6. The function registry makes this easy to add later. |
| Where to put MCP tests? | **Extend `test_mcp.py`** for consistency, or create `test_prompt_mcp.py` if it gets large. Implementer's choice. |
| Should `as_prompt()` accept `project`? | **Yes.** It overrides `self.project` for the underlying `recall()` call, same pattern as `remember()`. |
| Should budget estimation format-then-measure or estimate? | **Estimate using overhead constants.** Formatting then measuring is wasteful when most memories will be included anyway. The budget is advisory per PRD. |
