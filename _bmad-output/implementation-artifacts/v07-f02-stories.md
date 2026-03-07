# F2: Verbatim Recall — User Stories

## S1: RecallConfig + RecallResult Extended
**As a** developer  
**I want to** extend RecallConfig and RecallResult with verbatim flag  
**So that** the recall pipeline knows when to skip formatting

**Acceptance Criteria:**
- RecallConfig.verbatim: bool = False
- RecallResult includes: metadata, created_at, source, project, tier
- All existing fields preserved
- Backward compatible (default False)

**Estimate:** S

---

## S2: Recall Pipeline Conditional Formatting
**As a** developer  
**I want to** skip summarization when verbatim=True  
**So that** users get raw content

**Acceptance Criteria:**
- If verbatim: return raw `content` + metadata
- If not verbatim: use existing summarization logic
- Both paths tested
- No breaking changes

**Estimate:** M

---

## S3: CLI `--verbatim` Flag
**As a** user  
**I want to** run `lore recall 'query' --verbatim`  
**So that** I can get original words from CLI

**Acceptance Criteria:**
- Flag: --verbatim or -v
- Output: raw content + metadata (created_at, source, project)
- Formatted clearly (separators between results)
- Works with pagination (--limit, --offset)

**Estimate:** S

---

## S4: MCP Tool `verbatim` Parameter
**As a** Claude user  
**I want to** call `recall(query, verbatim=true)` via MCP  
**So that** I can get original words in conversations

**Acceptance Criteria:**
- MCP recall tool extended with verbatim: bool = False
- Parameter documented in tool description
- Works with all existing filters (topic, sentiment, etc.)
- Response includes metadata

**Estimate:** S

---

## S5: SDK Method with Verbatim
**As a** developer  
**I want to** call `lore.recall(query, verbatim=True)` in Python  
**So that** I can retrieve original content programmatically

**Acceptance Criteria:**
- Method signature: `recall(..., verbatim: bool = False)`
- Returns List[RecallResult] with raw content when verbatim=True
- Async compatible
- Docstring with examples

**Estimate:** S

---

## S6: Verbatim + All Existing Filters
**As a** user  
**I want to** combine verbatim with project/tier/topic/sentiment filters  
**So that** I can find original words on specific topics

**Acceptance Criteria:**
- Filters compose: project, type, tier, topic, sentiment, entity, category
- Works with date filters from F3 (when complete)
- Importance scoring still used for ranking
- No breaking changes

**Estimate:** S

---

## S7: As Prompt Integration with Verbatim
**As a** Claude user  
**I want to** inject verbatim recall into prompts  
**So that** I can use original words as context

**Acceptance Criteria:**
- `as_prompt()` respects verbatim flag
- Budget enforcement still works
- Clear indication: "These are your original words from [date]"
- Handles long raw content gracefully

**Estimate:** M

---

## S8: Metadata Display with Verbatim
**As a** user  
**I want to** see metadata (created_at, source, project) with verbatim content  
**So that** I can contextualize the memory

**Acceptance Criteria:**
- Always included: created_at, source, project, tier
- Format: header before content
- CLI: clear separators
- MCP: JSON structure with metadata fields

**Estimate:** S

---

## S9: Pagination for Verbatim
**As a** user  
**I want to** paginate through verbatim results  
**So that** I can browse long result sets

**Acceptance Criteria:**
- Parameters: limit, offset
- Default: 10 per page
- Works with CLI and SDK
- Total count in response

**Estimate:** S

---

## S10: Unit + Integration Tests
**As a** developer  
**I want to** comprehensive test coverage for verbatim  
**So that** bugs are caught early

**Acceptance Criteria:**
- Unit tests: verbatim flag handling, metadata inclusion
- Integration tests: verbatim + all filter combinations
- CLI tests: --verbatim flag, output format
- MCP tests: verbatim parameter, response structure
- As_prompt tests: budget enforcement with raw content
- Minimum 90% coverage

**Estimate:** M
