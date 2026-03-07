# F3: Temporal Recall Filters — Date Range Search PRD

## Overview
Add flexible date-range filtering to recall queries. Enable searches like "memories from March 2024" or "last 7 days" or "this year."

## User Stories

### S1: Absolute Date Range Filter
**As a** user  
**I want to** search memories between two dates  
**So that** I can find content from specific time periods

**Acceptance Criteria:**
- Parameters: `date_from`, `date_to` (ISO 8601 strings)
- Example: `recall(query, date_from="2024-01-01", date_to="2024-12-31")`
- SQL WHERE: `created_at BETWEEN ? AND ?`
- Time zone aware (use user's timezone or UTC)

### S2: Before/After Absolute Timestamps
**As a** user  
**I want to** search memories before or after a specific date  
**So that** I can filter by one boundary

**Acceptance Criteria:**
- Parameters: `before`, `after` (ISO 8601 timestamps)
- Example: `recall(query, after="2024-03-06T00:00:00Z")`
- Work independently or combined with date_from/date_to

### S3: Relative Time Filters (Days/Hours Ago)
**As a** user  
**I want to** search the last N days/hours  
**So that** I can avoid calculating exact dates

**Acceptance Criteria:**
- Parameters: `days_ago`, `hours_ago` (integers)
- Example: `recall(query, days_ago=7)` → memories from last 7 days
- Calculated as: `created_at >= NOW() - INTERVAL 'N days'`
- Zero means "today only"

### S4: Year/Month/Day Shorthand Filters
**As a** user  
**I want to** search by year or month without exact dates  
**So that** I can find seasonal patterns

**Acceptance Criteria:**
- Parameters: `year`, `month`, `day` (integers or strings)
- Example: `recall(query, year=2024, month=3)` → March 2024
- Example: `recall(query, month=12)` → all Decembers (any year)
- SQL: `EXTRACT(year FROM created_at) = ? AND EXTRACT(month FROM created_at) = ?`

### S5: Preset Time Windows
**As a** user  
**I want to** use preset windows like "last week"  
**So that** I don't have to calculate dates

**Acceptance Criteria:**
- Presets: `last_hour`, `last_day`, `last_week`, `last_month`, `last_year`, `today`
- Example: `recall(query, window="last_week")`
- Mapped to relative intervals (e.g., `last_week` → `days_ago=7`)

### S6: Combine Temporal Filters with Existing Filters
**As a** user  
**I want to** combine date filters with project/tier/topic filters  
**So that** I can narrow searches precisely

**Acceptance Criteria:**
- Filters compose: `recall(query, year=2024, project="work", topic="python")`
- All filters applied in WHERE clause (no post-filtering)
- Order: temporal + project + type + tier + metadata filters

### S7: Temporal Filters in CLI
**As a** user  
**I want to** use temporal flags from the command line  
**So that** I can search without writing code

**Acceptance Criteria:**
- Flags: `--year`, `--month`, `--day`, `--days-ago`, `--hours-ago`, `--before`, `--after`, `--window`
- Example: `lore recall 'query' --year 2024 --month 3 --days-ago 7`
- Combine with existing flags: `--project`, `--tier`, `--topic`, etc.

### S8: Temporal Filters in MCP Tool
**As a** Claude user  
**I want to** use temporal parameters in MCP recall  
**So that** I can time-bound searches in conversations

**Acceptance Criteria:**
- MCP parameters: year, month, day, days_ago, hours_ago, before, after, window
- Compose with existing filters
- Auto-completion for preset windows

### S9: SDK Temporal Filtering
**As a** developer  
**I want to** call `recall()` with temporal params in Python  
**So that** I can programmatically filter by date

**Acceptance Criteria:**
- Parameters: `year=None, month=None, day=None, days_ago=None, hours_ago=None, before=None, after=None, window=None`
- Example: `lore.recall("python", year=2024, month=3)`
- All SQL-level (WHERE clause), no post-filtering

### S10: Temporal Filters Respect Importance Scoring
**As a** user  
**I want to** get importance-ranked results within a time period  
**So that** most important memories appear first

**Acceptance Criteria:**
- Temporal filters apply to WHERE clause
- ORDER BY still uses importance_score
- Tier weights still applied
- Works with verbatim mode (F2)

## Technical Design
- **Storage:** `created_at` timestamp on all memories
- **Query Pattern:** Build WHERE clause with composable conditions
  - `(created_at BETWEEN ? AND ?) AND project = ? AND tier = ? ...`
- **Preset Mapping:**
  - `today` → `days_ago=0`
  - `last_hour` → `hours_ago=1`
  - `last_day` → `days_ago=1`
  - `last_week` → `days_ago=7`
  - `last_month` → `days_ago=30`
  - `last_year` → `days_ago=365`
- **Time Zone:** Use user's configured timezone or UTC
- **No Post-Filtering:** All logic in SQL WHERE clause for performance

## Acceptance
- 10 stories, S-M size
- No breaking changes
- Composes with all existing filters
- SQL-level filtering (performant)
- Zero LLM dependencies

