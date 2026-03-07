# F1: On This Day — Temporal Memory Recall PRD

## Overview
Enable users to discover what they were thinking, creating, and doing on this date across all previous years. Inspired by Reddit user's request for "one system that knows what day it is and surfaces what I was doing on this date across all previous years."

## User Stories

### S1: Basic On This Day Query
**As a** user  
**I want to** retrieve all memories created on March 6th across all years  
**So that** I can see patterns and progress over time

**Acceptance Criteria:**
- Query by month+day (no year)
- Results grouped by year, sorted descending (newest first)
- Include all memory types (notes, facts, conversations, etc.)
- Respect memory tier visibility
- Return memory ID, content summary, created_at, source

### S2: On This Day CLI Command
**As a** user  
**I want to** run `lore on-this-day` from the command line  
**So that** I can quickly access temporal memories without code

**Acceptance Criteria:**
- Command: `lore on-this-day [--month M] [--day D] [--project P] [--tier T]`
- Default: today's month+day
- Optional project, tier filters
- Output: formatted table or JSON
- Pagination support (--limit, --offset)

### S3: On This Day MCP Tool
**As a** Claude user  
**I want to** call `on_this_day` via MCP  
**So that** I can integrate temporal recall into AI conversations

**Acceptance Criteria:**
- MCP tool: `on_this_day(month?, day?, project?, tier?, limit?)`
- Returns structured JSON with year grouping
- Description: "Retrieve memories from this month+day across all years"

### S4: On This Day SDK Method
**As a** developer  
**I want to** call `lore.on_this_day(month=None, day=None, ...)` in Python  
**So that** I can programmatically access temporal memories

**Acceptance Criteria:**
- Method: `async def on_this_day(month=None, day=None, project=None, tier=None) -> List[Memory]`
- Returns memories grouped by year
- Respects all existing tier/visibility rules

### S5: Importance Weighting Within Year
**As a** user  
**I want to** see higher-importance memories first within each year group  
**So that** the most significant past events are easy to spot

**Acceptance Criteria:**
- Within each year, sort by importance_score DESC, created_at DESC
- Use existing multiplicative importance scoring
- Works with all tier weights

### S6: Date Window Configuration
**As a** user  
**I want to** fuzz the date match (+/- N days)  
**So that** I catch memories near the target date

**Acceptance Criteria:**
- Parameter: `date_window_days=1` (default)
- Query: `EXTRACT(month FROM created_at) = ? AND EXTRACT(day FROM created_at) BETWEEN ? AND ?`
- Configurable per call

### S7: As Prompt Integration
**As a** Claude user  
**I want to** inject on-this-day results into prompts  
**So that** I can use them as context for today's reflection

**Acceptance Criteria:**
- Results compatible with `as_prompt()` formatter
- Respects budget enforcement
- Includes metadata (created_at, source, project)

### S8: Multi-Project Support
**As a** user with multiple projects  
**I want to** filter on-this-day results by project  
**So that** I can see project-specific temporal patterns

**Acceptance Criteria:**
- Optional `project` parameter
- Returns only memories in specified project
- Default: all projects

## Technical Design
- **Storage:** Use existing `memories` table with `created_at` timestamp
- **Query:** `SELECT * FROM memories WHERE EXTRACT(month FROM created_at) = ? AND EXTRACT(day FROM created_at) BETWEEN ? AND ? ORDER BY EXTRACT(year FROM created_at) DESC, importance_score DESC`
- **Tier Visibility:** Respect `valid_until` timestamps + tier visibility rules
- **No LLM required:** Pure SQL + timestamp filtering

## Acceptance
- 8 stories, S-M size
- No breaking changes
- All existing features (importance, tiers, project filters) integrated
- Works with all memory types
- Zero API key dependencies
