# F1: On This Day — User Stories

## S1: OnThisDayEngine Class
**As a** developer  
**I want to** have an OnThisDayEngine that queries memories by month+day  
**So that** temporal recall is encapsulated and testable

**Acceptance Criteria:**
- Class in `src/lore/temporal.py`
- Method: `on_this_day(month, day, project, tier, date_window_days, limit, offset)`
- Returns `Dict[int, List[Memory]]` grouped by year
- Respects tier visibility and archived status
- Orders by year DESC, then importance_score DESC

**Estimate:** M

---

## S2: SQL Query for Month+Day Extraction
**As a** developer  
**I want to** query memories matching month+day with date window  
**So that** results include fuzzy date matches

**Acceptance Criteria:**
- EXTRACT(month FROM created_at) = ?
- EXTRACT(day FROM created_at) BETWEEN ? AND ?
- Handles leap years correctly
- Date window default 1 day
- Supports all databases (SQLite, HTTP store)

**Estimate:** S

---

## S3: Python Grouping by Year
**As a** developer  
**I want to** group query results by year in Python  
**So that** response is year-organized

**Acceptance Criteria:**
- Results grouped: {2024: [mem1, mem2], 2023: [mem3], ...}
- Sorted by year DESC
- Within each year, sorted by importance
- All memory fields preserved

**Estimate:** S

---

## S4: CLI Command `lore on-this-day`
**As a** user  
**I want to** run `lore on-this-day [--month] [--day] [--project] [--tier]`  
**So that** I can access temporal memories from CLI

**Acceptance Criteria:**
- Subcommand: `cmd_on_this_day(args)`
- Flags: --month, --day, --project, --tier, --limit, --offset, --json
- Default: today's month+day
- Output: formatted table + JSON option
- Error handling for invalid dates

**Estimate:** M

---

## S5: MCP Tool `on_this_day`
**As a** Claude user  
**I want to** call `on_this_day(month, day, ...)` via MCP  
**So that** I can retrieve temporal memories in conversations

**Acceptance Criteria:**
- Tool in `src/lore/mcp/server.py`
- Parameters: month, day, project, tier, limit
- Returns: JSON with year grouping
- Description: "Retrieve memories from month+day across all years"
- Auto-complete friendly

**Estimate:** S

---

## S6: SDK Method `lore.on_this_day()`
**As a** developer  
**I want to** call `lore.on_this_day(month=None, day=None, ...)` in Python  
**So that** I can programmatically access temporal memories

**Acceptance Criteria:**
- Method in `src/lore/lore.py`
- Signature: `async def on_this_day(...) -> Dict[int, List[Memory]]`
- Delegates to OnThisDayEngine
- Async/await compatible
- Docstring with examples

**Estimate:** S

---

## S7: Tier Visibility Integration
**As a** a developer  
**I want to** respect tier visibility + archived status in on-this-day  
**So that** users don't see expired or deleted memories

**Acceptance Criteria:**
- Filter by: valid_until IS NULL
- Filter by: archived IS NULL
- Filter by tier if provided
- Use existing tier weights for importance ordering

**Estimate:** S

---

## S8: As Prompt Integration
**As a** Claude user  
**I want to** inject on-this-day results into prompts  
**So that** I can use them as context for reflection

**Acceptance Criteria:**
- Works with `as_prompt()` formatter
- Respects budget enforcement
- Includes metadata (created_at, source, project)
- Formatted for readability

**Estimate:** S

---

## S9: Unit + Integration Tests
**As a** a developer  
**I want to** comprehensive test coverage for on-this-day  
**So that** bugs are caught early

**Acceptance Criteria:**
- Unit tests: query building, grouping, edge cases (leap year, etc.)
- Integration tests: full pipeline with real data
- CLI tests: argument parsing, output format
- MCP tests: tool call, response structure
- Minimum 90% coverage

**Estimate:** M

---

## S10: Documentation + Examples
**As a** a user  
**I want to** understand how to use on-this-day  
**So that** I can quickly adopt the feature

**Acceptance Criteria:**
- Docstring in SDK method
- CLI help text
- MCP tool description
- Example: `lore on-this-day --month 3 --day 6`
- Example: Python `lore.on_this_day(month=3, day=6)`
- Example: MCP in Claude conversation

**Estimate:** S
