# QA Report: F10 - Memory Export / Prompt Formatting

**Feature:** F10 - Memory Export / Prompt Formatting (`as_prompt`)
**Date:** 2026-03-06
**Tester:** Quinn (QA Engineer)
**Branch:** feature/v0.6.0-open-brain
**Verdict:** PASS

---

## 1. Test Execution Summary

### F10 Tests (`tests/test_prompt_formatter.py` + `tests/test_prompt_mcp.py`)

| Suite | Tests | Passed | Failed | Skipped |
|-------|-------|--------|--------|---------|
| TestXMLFormat | 5 | 5 | 0 | 0 |
| TestChatMLFormat | 3 | 3 | 0 | 0 |
| TestMarkdownFormat | 3 | 3 | 0 | 0 |
| TestRawFormat | 4 | 4 | 0 | 0 |
| TestBudgetEnforcement | 8 | 8 | 0 | 0 |
| TestFiltering | 7 | 7 | 0 | 0 |
| TestAsPromptIntegration | 4 | 4 | 0 | 0 |
| TestMCPAsPrompt | 6 | 6 | 0 | 0 |
| **Total** | **40** | **40** | **0** | **0** |

### Full Test Suite Regression Check

| Metric | Value |
|--------|-------|
| Total tests | 713 |
| Passed | 697 |
| Failed | 2 (pre-existing, unrelated) |
| Skipped | 14 |
| Duration | 194.86s |

**Note:** The 2 failures are in `tests/test_http_store.py` (HTTP timeout errors against external service). These are pre-existing and unrelated to F10.

---

## 2. Story-by-Story Acceptance Criteria Verification

### S1: Formatter Infrastructure and FORMAT_REGISTRY

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| AC-S1.1 | Registry dispatches format functions | PASS | `FORMAT_REGISTRY` contains all 4 formats; `test_format_registry_has_all_formats` verifies |
| AC-S1.2 | Unknown format raises ValueError | PASS | `test_unknown_format_raises` confirms `ValueError` with message |
| AC-S1.3 | Empty results return empty string | PASS | `test_empty_recall_returns_empty` confirms `""` |
| AC-S1.4 | min_score filtering | PASS | `test_min_score_filters` verifies scores [0.9, 0.5, 0.2] with min_score=0.4 yields 2 results |

### S2: XML Template Implementation

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| AC-S2.1 | Basic XML output | PASS | `test_basic_output` checks structure with correct types/scores |
| AC-S2.2 | XML is well-formed | PASS | `test_well_formed_xml` parses with `ET.fromstring()` |
| AC-S2.3 | Special characters escaped | PASS | `test_xml_escaping` verifies `<script>` is escaped and XML parses |
| AC-S2.4 | include_metadata adds attributes | PASS | `test_include_metadata` checks tags, id, created attributes |

### S3: ChatML Template Implementation

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| AC-S3.1 | Basic ChatML output | PASS | `test_basic_output` verifies `<\|im_start\|>`, header, entries, `<\|im_end\|>` |
| AC-S3.2 | include_metadata adds extra fields | PASS | `test_include_metadata` checks tags and id present |

### S4: Markdown and Raw Text Templates

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| AC-S4.1 | Markdown output | PASS | `test_basic_output` verifies heading and bullet format |
| AC-S4.2 | Raw output has no markup | PASS | `test_no_markup` asserts no `<`, `**`, `<\|im`, `#` characters |
| AC-S4.3 | Markdown include_metadata | PASS | `test_include_metadata` checks tags and id |
| AC-S4.4 | Raw include_metadata | PASS | `test_include_metadata` checks tags and id |

### S5: Context Budget Enforcement

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| AC-S5.1 | max_tokens limits output | PASS | `test_max_tokens_limits_output` with max_tokens=50 yields < 5 memories |
| AC-S5.2 | max_chars limits output | PASS | `test_max_chars_limits_output` with max_chars=300 yields < 5 memories |
| AC-S5.3 | Both budgets: stricter wins | PASS | `test_both_budgets_stricter_wins` confirms chars <= tokens output |
| AC-S5.4 | Score-descending order preserved | PASS | `test_score_descending_order` confirms highest-score memory present |
| AC-S5.5 | First memory always included | PASS | `test_first_memory_always_included` with 500-char memory and max_chars=100 |
| AC-S5.6 | No budget includes all | PASS | `test_no_budget_includes_all` confirms all 10 memories present |
| AC-S5.7 | Negative budget = no budget | PASS | `test_negative_budget_treated_as_no_budget` and `test_negative_max_chars_treated_as_no_budget` |

### S6: Lore.as_prompt() SDK Method

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| AC-S6.1 | Delegates to recall + PromptFormatter | PASS | Code review: `lore.py:446-466` calls `self.recall()` then `PromptFormatter().format()` |
| AC-S6.2 | All recall parameters pass through | PASS | `test_as_prompt_passes_recall_params` with type filter; code review confirms tags, type, limit passthrough |
| AC-S6.3 | Format/budget params pass to formatter | PASS | Code review: `lore.py:458-466` passes all params; integration tests exercise this path |
| AC-S6.4 | Returns empty string on no matches | PASS | `test_as_prompt_returns_empty_on_no_matches` confirms `""` |

### S7: MCP Tool, CLI Command, and Integration Tests

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| AC-S7.1 | as_prompt tool registered | PASS | `test_as_prompt_tool_exists` confirms callable |
| AC-S7.2 | Tool returns formatted string directly | PASS | `test_as_prompt_returns_formatted` + `test_as_prompt_no_wrapping` |
| AC-S7.3 | Error handling | PASS | `test_as_prompt_error_handling` confirms "Failed to format memories" on invalid format |
| AC-S7.4 | `lore prompt` basic usage | PASS | Code review: CLI `prompt` subcommand registered, defaults to xml format |
| AC-S7.5 | All CLI flags work | PASS | Code review: `cli.py:278-287` defines all flags (format, max-tokens, max-chars, limit, type, tags, min-score, include-metadata, project) |
| AC-S7.6 | Pipe-friendly output | PASS | Code review: `cli.py:358` uses `print(result, end="")` — no decoration |
| AC-S7.7 | End-to-end with MemoryStore | PASS | `test_end_to_end_with_memory_store` stores 3 memories, confirms all 3 in output |
| AC-S7.8 | Template unit tests | PASS | 15 template tests across all 4 formats |
| AC-S7.9 | Budget enforcement tests | PASS | 8 budget tests covering all scenarios |
| AC-S7.10 | Edge case tests | PASS | 7 filtering/edge-case tests |
| AC-S7.11 | MCP tool tests | PASS | 6 MCP tests covering existence, output, errors, empty results |

---

## 3. PRD Traceability

All 23 PRD acceptance criteria (AC-1 through AC-23) are traced in the story document's traceability table. Each maps to a specific story AC with corresponding test coverage.

---

## 4. Code Quality Assessment

### Strengths
- Clean separation: templates in `templates.py`, budget logic in `formatter.py`, SDK method in `lore.py`
- XML escaping uses stdlib `xml.sax.saxutils.escape()` and `quoteattr()` — secure against injection
- Budget enforcement is a clean single-pass algorithm with at-least-one guarantee
- No third-party dependencies added
- `print(result, end="")` ensures pipe-friendly CLI output

### Minor Observations (non-blocking)
- `_effective_budget` uses `token_budget or char_budget` on line 84 of `formatter.py` which would return `char_budget` if `token_budget` is `0`, but this is fine because `0` is already filtered out by the `> 0` check on line 75
- MCP tool intentionally omits `max_chars` parameter per architecture doc — acceptable

### Security
- XML content properly escaped via `escape()` / `quoteattr()`
- No user input passed to shell or eval
- No new dependencies introduced

---

## 5. Breaking Changes

**None detected.** Full test suite (697 passed) shows no regressions from F10 changes.

---

## 6. Final Verdict

**PASS**

All 40 F10-specific tests pass. All 35 acceptance criteria across 7 stories are verified. All 23 PRD acceptance criteria are traced. No regressions. Code quality is solid with proper security practices.
