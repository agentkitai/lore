# F10 — Prompt Export: User Stories

**Feature:** F10 — Memory Export / Prompt Formatting (`as_prompt`)
**Version:** v0.6.0 ("Open Brain")
**Created:** 2026-03-06
**PRD:** `_bmad-output/planning-artifacts/f10-prompt-export-prd.md`
**Architecture:** `_bmad-output/implementation-artifacts/f10-prompt-export-architecture.md`

---

## Sprint Order

```
Sprint 1 (parallelizable):
  S1: Formatter Infrastructure + FORMAT_REGISTRY
  S2: XML Template
  S3: ChatML Template
  S4: Markdown + Raw Templates

Sprint 2 (parallelizable after Sprint 1):
  S5: Context Budget Enforcement
  S6: Lore.as_prompt() SDK Method

Sprint 3 (parallelizable after S6):
  S7: MCP Tool + CLI Command + Integration Tests
```

---

## S1: Formatter Infrastructure and FORMAT_REGISTRY

**Size:** S
**Dependencies:** None

As a developer, I want a `PromptFormatter` class with a `FORMAT_REGISTRY` so that format functions can be registered and dispatched by name.

### Acceptance Criteria

**AC-S1.1** — Registry dispatches format functions
- **Given** `FORMAT_REGISTRY` contains entries for "xml", "chatml", "markdown", "raw"
- **When** `PromptFormatter.format()` is called with `format="xml"`
- **Then** it dispatches to the registered `format_xml` function

**AC-S1.2** — Unknown format raises ValueError
- **Given** a `PromptFormatter` instance
- **When** `format()` is called with `format="html"`
- **Then** it raises `ValueError` with message listing valid formats
- Maps to: PRD AC-2

**AC-S1.3** — Empty results return empty string
- **Given** an empty list of `RecallResult`
- **When** `format()` is called with any valid format
- **Then** it returns `""`
- Maps to: PRD AC-8

**AC-S1.4** — min_score filtering
- **Given** results with scores [0.9, 0.5, 0.2]
- **When** `format()` is called with `min_score=0.4`
- **Then** only results with scores >= 0.4 are passed to the format function
- Maps to: PRD AC-7

### Implementation Notes

- Create `src/lore/prompt/__init__.py` re-exporting `PromptFormatter` and `FORMAT_REGISTRY`
- Create `src/lore/prompt/formatter.py` with `PromptFormatter` class
- Create `src/lore/prompt/templates.py` with `FormatFn` type alias, `FORMAT_REGISTRY` dict, and stub functions (stubs return `""` — filled by S2-S4)
- `PromptFormatter.format()` handles: validate format name, filter by min_score, delegate to registry

### Files

- `src/lore/prompt/__init__.py` (new)
- `src/lore/prompt/formatter.py` (new)
- `src/lore/prompt/templates.py` (new)

---

## S2: XML Template Implementation

**Size:** S
**Dependencies:** S1

As an AI agent using Claude, I want memories formatted as well-formed XML so I can inject them directly into my context with optimal structure for Claude's XML preference.

### Acceptance Criteria

**AC-S2.1** — Basic XML output
- **Given** 2 recall results with content "Deploy via CI" (type=lesson, score=0.87) and "Prod is us-east-1" (type=fact, score=0.72)
- **When** `format_xml(query="deployment", results, include_metadata=False)` is called
- **Then** output matches the structure:
  ```xml
  <memories query="deployment">
  <memory type="lesson" score="0.87">Deploy via CI</memory>
  <memory type="fact" score="0.72">Prod is us-east-1</memory>
  </memories>
  ```
- Maps to: PRD AC-1

**AC-S2.2** — XML is well-formed
- **Given** any set of recall results
- **When** `format_xml()` produces output
- **Then** `xml.etree.ElementTree.fromstring()` parses it without error
- Maps to: PRD AC-12

**AC-S2.3** — Special characters escaped
- **Given** a memory containing `<script>alert("x")&foo</script>`
- **When** `format_xml()` produces output
- **Then** content has `<`, `>`, `&`, `"` escaped and output remains well-formed XML

**AC-S2.4** — include_metadata adds attributes
- **Given** `include_metadata=True`
- **When** `format_xml()` is called
- **Then** each `<memory>` element includes `tags`, `id`, and `created` attributes
- Maps to: PRD AC-10

### Implementation Notes

- Use `xml.sax.saxutils.escape()` for content, `quoteattr()` for attributes
- No third-party XML libraries

### Files

- `src/lore/prompt/templates.py` (edit — implement `format_xml`)

---

## S3: ChatML Template Implementation

**Size:** S
**Dependencies:** S1

As a developer building agents for OpenAI-style models, I want memories formatted in ChatML so I can inject them as system messages with proper `<|im_start|>` / `<|im_end|>` markers.

### Acceptance Criteria

**AC-S3.1** — Basic ChatML output
- **Given** 2 recall results
- **When** `format_chatml(query="testing", results, include_metadata=False)` is called
- **Then** output matches:
  ```
  <|im_start|>system
  Relevant memories for: testing

  [lesson, 0.87] Content one.
  [fact, 0.72] Content two.
  <|im_end|>
  ```

**AC-S3.2** — include_metadata adds extra fields
- **Given** `include_metadata=True`
- **When** `format_chatml()` is called
- **Then** each entry includes tags and id in addition to type and score

### Files

- `src/lore/prompt/templates.py` (edit — implement `format_chatml`)

---

## S4: Markdown and Raw Text Templates

**Size:** S
**Dependencies:** S1

As a developer, I want markdown and raw text format options so I can use memories in documentation tooling, README generation, or plain-text contexts.

### Acceptance Criteria

**AC-S4.1** — Markdown output
- **Given** 2 recall results
- **When** `format_markdown(query="patterns", results, include_metadata=False)` is called
- **Then** output matches:
  ```markdown
  ## Relevant Memories: patterns

  - **[lesson, 0.87]** Content one.
  - **[fact, 0.72]** Content two.
  ```

**AC-S4.2** — Raw output has no markup
- **Given** 2 recall results
- **When** `format_raw()` is called
- **Then** output contains no XML tags, no markdown formatting, no ChatML markers — just query header and plain content separated by blank lines

**AC-S4.3** — Markdown include_metadata
- **Given** `include_metadata=True`
- **When** `format_markdown()` is called
- **Then** each entry includes tags and id

**AC-S4.4** — Raw include_metadata
- **Given** `include_metadata=True`
- **When** `format_raw()` is called
- **Then** entries include type, score, tags, and id as prefix text

### Files

- `src/lore/prompt/templates.py` (edit — implement `format_markdown`, `format_raw`)

---

## S5: Context Budget Enforcement

**Size:** M
**Dependencies:** S1

As an AI agent, I want to specify a token or character budget so that the exported prompt fits within my available context window.

### Acceptance Criteria

**AC-S5.1** — max_tokens limits output
- **Given** 5 memories, each ~100 chars of content
- **When** `format()` is called with `max_tokens=50` (~200 chars)
- **Then** output contains fewer memories than the full set, and total character count is approximately within 20% of `50 * 4 = 200` chars
- Maps to: PRD AC-3

**AC-S5.2** — max_chars limits output
- **Given** 5 memories
- **When** `format()` is called with `max_chars=300`
- **Then** output is at most ~300 characters (accounting for overhead estimation variance)
- Maps to: PRD AC-4

**AC-S5.3** — Both budgets: stricter wins
- **Given** `max_tokens=100` (400 chars) and `max_chars=200`
- **When** `format()` is called
- **Then** effective budget is 200 chars (the stricter limit)
- Maps to: PRD AC-5

**AC-S5.4** — Score-descending order preserved
- **Given** results with scores [0.9, 0.7, 0.5, 0.3]
- **When** budget allows only 2 memories
- **Then** the two with scores 0.9 and 0.7 are included
- Maps to: PRD AC-6

**AC-S5.5** — First memory always included
- **Given** a single matching memory of 500 chars
- **When** `max_chars=100`
- **Then** the memory is still included (at-least-one guarantee)
- Maps to: PRD AC-9

**AC-S5.6** — No budget includes all
- **Given** 10 memories and no `max_tokens` or `max_chars`
- **When** `format()` is called
- **Then** all 10 memories appear in the output

**AC-S5.7** — Negative budget treated as no budget
- **Given** `max_tokens=-1`
- **When** `format()` is called
- **Then** behaves as if no budget was set (no error, no truncation)

### Implementation Notes

- Budget enforcement is in `PromptFormatter.format()`, before calling the format function
- Token-to-char conversion: `max_tokens * 4`
- Use `_OVERHEAD_CHARS` and `_WRAPPER_CHARS` constants per architecture doc section 6
- Estimate per-entry cost as `len(result.memory.content) + _OVERHEAD_CHARS[format]`
- Single linear pass; results already sorted by recall()

### Files

- `src/lore/prompt/formatter.py` (edit — add budget logic)
- `src/lore/prompt/templates.py` (edit — add `_OVERHEAD_CHARS`, `_WRAPPER_CHARS` constants)

---

## S6: Lore.as_prompt() SDK Method

**Size:** S
**Dependencies:** S1, S5

As a developer using the Lore SDK, I want a `lore.as_prompt()` method that retrieves and formats memories in one call so I don't have to manually call `recall()` and format results.

### Acceptance Criteria

**AC-S6.1** — as_prompt delegates to recall and PromptFormatter
- **Given** a `Lore` instance with stored memories
- **When** `lore.as_prompt("query", format="xml")` is called
- **Then** it internally calls `self.recall()` with correct params and returns formatted output

**AC-S6.2** — All recall parameters pass through
- **Given** `as_prompt(query, tags=["t1"], type="lesson", limit=5)`
- **When** the method executes
- **Then** `recall()` is called with `tags=["t1"]`, `type="lesson"`, `limit=5`
- Maps to: PRD AC-11

**AC-S6.3** — Format and budget params pass to formatter
- **Given** `as_prompt(query, format="markdown", max_tokens=500, min_score=0.3, include_metadata=True)`
- **When** the method executes
- **Then** `PromptFormatter.format()` receives all these parameters

**AC-S6.4** — Returns empty string on no matches
- **Given** no stored memories
- **When** `as_prompt("anything")` is called
- **Then** returns `""`
- Maps to: PRD AC-8

### Implementation Notes

- Add method to `Lore` class in `src/lore/lore.py`
- `PromptFormatter()` is stateless — instantiate per-call, no caching needed
- `project` parameter overrides `self.project` for the `recall()` call

### Files

- `src/lore/lore.py` (edit — add `as_prompt()` method)

---

## S7: MCP Tool, CLI Command, and Integration Tests

**Size:** M
**Dependencies:** S6

As an MCP tool consumer or CLI user, I want to access prompt export through MCP and CLI interfaces, with comprehensive tests covering all layers.

### Acceptance Criteria

#### MCP Tool

**AC-S7.1** — as_prompt tool registered
- **Given** the MCP server is running
- **When** tools are listed
- **Then** `as_prompt` appears with correct description
- Maps to: PRD AC-13

**AC-S7.2** — Tool returns formatted string directly
- **Given** matching memories exist
- **When** `as_prompt` MCP tool is called
- **Then** it returns the formatted string with no wrapping status text
- Maps to: PRD AC-14

**AC-S7.3** — Error handling
- **Given** `recall()` raises an exception
- **When** `as_prompt` MCP tool is called
- **Then** it returns `"Failed to format memories: <error>"` (not an exception)
- Maps to: PRD AC-15

#### CLI Command

**AC-S7.4** — `lore prompt` basic usage
- **Given** memories exist
- **When** `lore prompt 'query'` is run
- **Then** XML-formatted output is printed to stdout
- Maps to: PRD AC-16

**AC-S7.5** — All CLI flags work
- **Given** memories exist
- **When** `lore prompt 'q' --format markdown --max-tokens 500 --max-chars 1000 --limit 3 --type lesson --tags t1,t2 --min-score 0.5 --include-metadata`
- **Then** all flags are correctly passed through to `as_prompt()`
- Maps to: PRD AC-17

**AC-S7.6** — Pipe-friendly output
- **Given** memories exist
- **When** `lore prompt 'query'` is run
- **Then** stdout contains only the formatted string — no status messages, no decorations
- Maps to: PRD AC-18

#### Integration Tests

**AC-S7.7** — End-to-end with MemoryStore
- **Given** a `Lore` instance with `MemoryStore`, 3 remembered items
- **When** `as_prompt()` is called
- **Then** output contains all 3 memories in formatted structure
- Maps to: PRD AC-22

#### Unit Tests (all layers)

**AC-S7.8** — Template unit tests
- Tests for each format (xml, chatml, markdown, raw) covering basic output, metadata, empty results, and XML escaping
- Maps to: PRD AC-19

**AC-S7.9** — Budget enforcement tests
- Tests for max_tokens, max_chars, both, neither, first-memory guarantee, score ordering
- Maps to: PRD AC-20

**AC-S7.10** — Edge case tests
- No results, single result exceeding budget, min_score filtering, unknown format
- Maps to: PRD AC-21

**AC-S7.11** — MCP tool tests
- Tool exists, returns formatted output, error handling, empty results
- Maps to: PRD AC-23

### Implementation Notes

- MCP tool: follow existing pattern in `server.py`, `max_chars` intentionally omitted from MCP interface
- CLI: add `prompt` subcommand to `build_parser()` and `cmd_prompt()` handler
- Tests: create `tests/test_prompt_formatter.py` for formatter/template/budget tests
- Tests: extend `tests/test_mcp.py` or create `tests/test_prompt_mcp.py` for MCP tests
- Use `_make_results()` test helper for constructing `RecallResult` fixtures

### Files

- `src/lore/mcp/server.py` (edit — add `as_prompt` tool)
- `src/lore/cli.py` (edit — add `prompt` subcommand)
- `tests/test_prompt_formatter.py` (new)
- `tests/test_prompt_mcp.py` (new, or extend `tests/test_mcp.py`)

---

## PRD Acceptance Criteria Traceability

| PRD AC | Story | Story AC |
|--------|-------|----------|
| AC-1   | S2    | AC-S2.1  |
| AC-2   | S1    | AC-S1.2  |
| AC-3   | S5    | AC-S5.1  |
| AC-4   | S5    | AC-S5.2  |
| AC-5   | S5    | AC-S5.3  |
| AC-6   | S5    | AC-S5.4  |
| AC-7   | S1    | AC-S1.4  |
| AC-8   | S1/S6 | AC-S1.3, AC-S6.4 |
| AC-9   | S5    | AC-S5.5  |
| AC-10  | S2    | AC-S2.4  |
| AC-11  | S6    | AC-S6.2  |
| AC-12  | S2    | AC-S2.2  |
| AC-13  | S7    | AC-S7.1  |
| AC-14  | S7    | AC-S7.2  |
| AC-15  | S7    | AC-S7.3  |
| AC-16  | S7    | AC-S7.4  |
| AC-17  | S7    | AC-S7.5  |
| AC-18  | S7    | AC-S7.6  |
| AC-19  | S7    | AC-S7.8  |
| AC-20  | S7    | AC-S7.9  |
| AC-21  | S7    | AC-S7.10 |
| AC-22  | S7    | AC-S7.7  |
| AC-23  | S7    | AC-S7.11 |
