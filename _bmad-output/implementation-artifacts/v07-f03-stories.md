# F3: Temporal Recall Filters — User Stories

## S1: Absolute Date Range Filter
**As a** developer
**I want to** filter recall queries by `date_from` and `date_to` ISO 8601 strings
**So that** I can search memories within a specific date range

**Acceptance Criteria:**
- Add `date_from` and `date_to` fields (Optional[str]) to `RecallConfig` in `src/lore/types.py`
- Parse ISO 8601 strings to datetime objects
- SQL WHERE: `created_at BETWEEN ? AND ?`
- If only `date_from` provided, filter `created_at >= ?`
- If only `date_to` provided, filter `created_at <= ?`
- Timezone-aware parsing (default to UTC if no timezone specified)
- Returns empty list (not error) when no memories match

**Estimate:** M

---

## S2: Before/After Absolute Timestamps
**As a** developer
**I want to** filter recall queries with `before` and `after` ISO 8601 timestamps
**So that** I can filter by a single temporal boundary

**Acceptance Criteria:**
- Add `before` and `after` fields (Optional[str]) to `RecallConfig`
- `before`: `created_at < ?` (exclusive upper bound)
- `after`: `created_at >= ?` (inclusive lower bound)
- Can be used independently or together
- Compose with `date_from`/`date_to` (all conditions ANDed)
- Timezone-aware parsing (default to UTC)

**Estimate:** S

---

## S3: Relative Time Filters (Days/Hours Ago)
**As a** developer
**I want to** filter recall queries by `days_ago` or `hours_ago` integers
**So that** users can search recent memories without calculating exact dates

**Acceptance Criteria:**
- Add `days_ago` and `hours_ago` fields (Optional[int]) to `RecallConfig`
- `days_ago=7` resolves to `created_at >= NOW() - 7 days`
- `hours_ago=3` resolves to `created_at >= NOW() - 3 hours`
- `days_ago=0` means "today only" (from start of current day)
- Both can be combined (additive: 2 days + 3 hours)
- Negative values rejected with validation error

**Estimate:** S

---

## S4: Year/Month/Day Shorthand Filters
**As a** developer
**I want to** filter recall queries by `year`, `month`, and `day` integers
**So that** users can search by calendar components without constructing date ranges

**Acceptance Criteria:**
- Add `year`, `month`, `day` fields (Optional[int]) to `RecallConfig`
- `year=2024, month=3` resolves to March 1–31, 2024
- `month=12` alone matches all Decembers (any year)
- `year=2024` alone matches all of 2024
- `year=2024, month=3, day=6` matches single day
- Handles month boundaries correctly (28/29/30/31 days)
- Invalid values (month=13, day=32) rejected with validation error

**Estimate:** M

---

## S5: Preset Time Windows
**As a** developer
**I want to** support a `window` parameter with preset time ranges
**So that** users can use shortcuts like "last_week" instead of calculating dates

**Acceptance Criteria:**
- Add `window` field (Optional[str]) to `RecallConfig`
- Supported presets: `today`, `last_hour`, `last_day`, `last_week`, `last_month`, `last_year`
- Preset mapping: `today` → start of today, `last_hour` → 1 hour ago, `last_day` → 24 hours ago, `last_week` → 7 days ago, `last_month` → 30 days ago, `last_year` → 365 days ago
- Invalid window value rejected with validation error listing valid options
- Window is lower priority than explicit date params (explicit params override)

**Estimate:** S

---

## S6: TemporalFilterResolver + SQL WHERE Integration
**As a** developer
**I want to** a `TemporalFilterResolver` class that converts all temporal params to SQL WHERE conditions
**So that** temporal filtering is encapsulated, testable, and composable with existing filters

**Acceptance Criteria:**
- Create `src/lore/temporal.py` with `TemporalFilterResolver` class
- Method: `resolve(config) -> Tuple[Optional[datetime], Optional[datetime]]`
- Priority order: explicit dates > before/after > relative times > year/month/day > presets
- Add `_build_temporal_where(config)` to `src/lore/store/sqlite.py`
- Temporal WHERE conditions ANDed with existing project/tier/type/metadata conditions
- All filtering in SQL (no post-filtering in Python)
- `created_at` column must be indexed for performance

**Estimate:** M

---

## S7: CLI Temporal Flags
**As a** user
**I want to** use temporal flags on the `lore recall` command
**So that** I can time-bound searches from the command line

**Acceptance Criteria:**
- Add flags to recall subcommand: `--year`, `--month`, `--day`, `--days-ago`, `--hours-ago`, `--before`, `--after`, `--window`, `--date-from`, `--date-to`
- Example: `lore recall "python" --year 2024 --month 3`
- Example: `lore recall "python" --days-ago 7`
- Example: `lore recall "python" --window last_week`
- Flags compose with existing: `--project`, `--tier`, `--topic`, etc.
- Help text documents each flag with examples
- Invalid flag values produce clear error messages

**Estimate:** M

---

## S8: MCP Tool Temporal Parameters
**As a** Claude user
**I want to** pass temporal parameters to the MCP `recall` tool
**So that** I can time-bound memory searches in conversations

**Acceptance Criteria:**
- Add parameters to MCP recall tool in `src/lore/mcp/server.py`: year, month, day, days_ago, hours_ago, window, before, after, date_from, date_to
- All parameters optional with None defaults
- Compose with existing MCP filters (project, tier, topic, etc.)
- Tool description updated to mention temporal filtering
- Parameter descriptions include examples and valid values
- Window parameter description lists valid presets

**Estimate:** S

---

## S9: SDK `recall()` Temporal Parameters
**As a** developer
**I want to** pass temporal parameters to `lore.recall()` in Python
**So that** I can programmatically filter memories by date

**Acceptance Criteria:**
- Add temporal keyword args to `recall()` in `src/lore/lore.py`: `year`, `month`, `day`, `days_ago`, `hours_ago`, `window`, `before`, `after`, `date_from`, `date_to`
- All params default to None (backward compatible)
- Params forwarded to `RecallConfig` and resolved via `TemporalFilterResolver`
- Example: `lore.recall("python", year=2024, month=3)`
- Async/await compatible
- Docstring with parameter descriptions and examples

**Estimate:** S

---

## S10: Unit + Integration Tests
**As a** developer
**I want to** comprehensive test coverage for temporal filters
**So that** all filter modes and edge cases are verified

**Acceptance Criteria:**
- Create `tests/test_temporal_filters.py`
- Unit tests for `TemporalFilterResolver`: presets, date parsing, year/month/day, relative times, priority order
- Unit tests for `_build_temporal_where`: SQL generation, parameter binding
- Integration tests: temporal filters with real recall queries returning correct results
- Composition tests: temporal + project + tier + topic filters combined
- Edge cases: leap year (Feb 29), month boundaries, timezone handling, `days_ago=0`
- Verify importance_score ordering preserved within temporal windows
- Verify compatibility with verbatim mode (F2)
- CLI tests: flag parsing and validation
- MCP tests: parameter passing and response structure
- Minimum 90% coverage for new code

**Estimate:** M
