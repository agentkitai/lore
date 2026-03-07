# F2: Verbatim Recall — Original Word Preservation PRD

## Overview
Return users' original words from past memories instead of AI-generated summaries. Inspired by Reddit user's request: "return the answer in MY words, from MY past. Not Claude's answer. My answer."

## User Stories

### S1: Verbatim Recall Parameter
**As a** user  
**I want to** set `verbatim=True` on recall queries  
**So that** I get my original words without reformatting

**Acceptance Criteria:**
- Parameter: `recall(query, verbatim=True)`
- Default: `verbatim=False` (existing behavior)
- When True: return raw content, skip summarization
- Works with all existing recall filters (project, tier, topic, sentiment, etc.)

### S2: Verbatim CLI Flag
**As a** user  
**I want to** use `lore recall 'query' --verbatim`  
**So that** I can get original words from the command line

**Acceptance Criteria:**
- Flag: `--verbatim` or `-v`
- Output: raw content + metadata (created_at, source, project, tier)
- Pagination: `--limit N`

### S3: Verbatim MCP Tool Parameter
**As a** Claude user  
**I want to** call `recall(query, verbatim=true)` via MCP  
**So that** I can access my original words in conversations

**Acceptance Criteria:**
- MCP tool extended with `verbatim` boolean param
- Description updated: "Set verbatim=true to return original content unchanged"
- Works with all existing recall filters (topic, sentiment, entity, category, etc.)

### S4: Metadata Inclusion with Verbatim
**As a** user  
**I want to** see where the original content came from  
**So that** I can contextualize the memory

**Acceptance Criteria:**
- Include: `created_at`, `source`, `project`, `tier`, `memory_type`
- Include: `importance_score` (but not used for ranking in verbatim mode)
- Format: clearly separated from content

### S5: Verbatim + All Existing Filters
**As a** user  
**I want to** combine `verbatim=True` with topic/sentiment/entity filters  
**So that** I can find my original words on specific topics

**Acceptance Criteria:**
- Filters work: project, type, tier, topic, sentiment, entity, category
- Date range filters (from F3) work with verbatim
- Importance scoring still applied (for ranking), but content unchanged

### S6: Pagination for Verbatim Results
**As a** user  
**I want to** paginate through verbatim results  
**So that** I can browse long result sets

**Acceptance Criteria:**
- Parameters: `limit`, `offset` or `page`
- Default: 10 results per page
- Total count in response

### S7: SDK Method for Verbatim Recall
**As a** developer  
**I want to** call `lore.recall(query, verbatim=True)` in Python  
**So that** I can programmatically retrieve original words

**Acceptance Criteria:**
- Method signature: `recall(..., verbatim: bool = False) -> List[Memory]`
- Returns raw `content` field + metadata
- No summarization in returned objects

### S8: As Prompt Integration with Verbatim
**As a** Claude user  
**I want to** inject verbatim recall results into prompts  
**So that** I can use my original words as context

**Acceptance Criteria:**
- `as_prompt()` respects verbatim flag
- Budget enforcement still works
- Clearly marks content as verbatim (optional: "This is your original content from [date]")

## Technical Design
- **Storage:** Raw `content` field already exists in `Memory` model
- **Query Logic:** Add `verbatim: bool = False` param to `recall()` method
- **Formatting:** Skip any summarization steps when `verbatim=True`
- **Response:** Return `Memory` objects with full metadata
- **No Breaking Changes:** Default `verbatim=False` preserves existing behavior
- **No LLM Required:** Pure retrieval + filtering

## Acceptance
- 8 stories, S-M size
- No breaking changes (default behavior unchanged)
- Combines seamlessly with all existing filters
- Backward compatible API

