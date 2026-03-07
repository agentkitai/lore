# F2: Verbatim Recall — Architecture Document

## Overview
Add `verbatim` mode to recall pipeline to return raw user content without AI reformatting.

## Components

### 1. RecallConfig Extended
File: `src/lore/types.py`
```python
@dataclass
class RecallConfig:
    query: str
    project: Optional[str] = None
    memory_type: Optional[str] = None
    tier: Optional[str] = None
    topic: Optional[str] = None
    sentiment: Optional[str] = None
    entity: Optional[str] = None
    category: Optional[str] = None
    limit: int = 10
    offset: int = 0
    # NEW:
    verbatim: bool = False  # When True, skip summarization
```

### 2. Recall Pipeline Integration
File: `src/lore/lore.py`, method `recall()`
```python
async def recall(
    self,
    query: str,
    ...,
    verbatim: bool = False,  # NEW
) -> List[RecallResult]:
    """Retrieve memories, optionally in verbatim mode."""
    
    config = RecallConfig(query=query, ..., verbatim=verbatim)
    
    # Existing search pipeline (semantic + filtering)
    memories = await self._retrieve_and_filter(config)
    
    # NEW: Skip formatting if verbatim
    if config.verbatim:
        # Return raw memories with minimal processing
        return [RecallResult(
            memory=mem,
            content=mem.content,  # Raw
            score=result_score,
            metadata=mem.metadata,
            created_at=mem.created_at,
            source=mem.source,
            project=mem.project,
            tier=mem.tier
        ) for mem in memories]
    else:
        # Existing summarization/formatting
        return await self._format_results(memories, config)
```

### 3. RecallResult Enhancement
```python
@dataclass
class RecallResult:
    memory: Memory
    content: str
    score: float
    # NEW: Metadata fields for verbatim clarity
    metadata: Dict[str, Any]
    created_at: datetime
    source: str
    project: str
    tier: str
    verbatim: bool = False
```

### 4. CLI Integration
File: `src/lore/cli.py`
```python
def cmd_recall(args):
    verbatim = args.verbatim  # Flag: --verbatim or -v
    results = lore.recall(
        args.query,
        verbatim=verbatim,
        ...
    )
    
    if args.verbatim:
        # Output: raw content + metadata
        for r in results:
            print(f"[{r.created_at}] {r.source} ({r.project})")
            print(r.content)
            print("---")
    else:
        # Existing formatted output
```

### 5. MCP Tool Parameter
File: `src/lore/mcp/server.py`
```python
@mcp_server.tool()
async def recall(
    query: str,
    project: Optional[str] = None,
    memory_type: Optional[str] = None,
    tier: Optional[str] = None,
    topic: Optional[str] = None,
    sentiment: Optional[str] = None,
    entity: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 10,
    offset: int = 0,
    # NEW:
    verbatim: bool = False,
) -> Dict[str, Any]:
    """Retrieve memories. Set verbatim=true for raw original content."""
    return await lore.recall(
        query=query,
        ...,
        verbatim=verbatim,
    )
```

### 6. SDK Method Signature
File: `src/lore/lore.py`
```python
async def recall(
    self,
    query: str,
    project: Optional[str] = None,
    memory_type: Optional[str] = None,
    tier: Optional[str] = None,
    topic: Optional[str] = None,
    sentiment: Optional[str] = None,
    entity: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 10,
    offset: int = 0,
    # NEW:
    verbatim: bool = False,  # Default: False (backward compatible)
) -> List[RecallResult]:
    """Retrieve memories. verbatim=True returns raw content unchanged."""
```

### 7. As Prompt Integration
File: `src/lore/prompt/formatter.py`
```python
async def format_for_prompt(
    self,
    results: List[RecallResult],
    budget_tokens: int,
    verbatim: bool = False,
) -> str:
    """Format recall results for prompt injection."""
    
    if verbatim:
        # Mark as verbatim, include raw content
        output = "These are your original words:\n\n"
        for r in results:
            output += f"[{r.created_at.strftime('%Y-%m-%d')}] {r.source}:\n"
            output += r.content + "\n\n"
    else:
        # Existing summary formatting
        output = await self._format_summarized(results, budget_tokens)
    
    return self._enforce_budget(output, budget_tokens)
```

### 8. Filter Composition
- Verbatim mode composes with all existing filters
- Filters applied in WHERE clause (same as current recall)
- Example: `recall("python", topic="learning", verbatim=True)` → raw content with topic="learning"

### 9. Pagination for Verbatim
- Use existing pagination (limit, offset)
- Work normally with verbatim results (they can be longer)
- CLI: `--limit 20 --offset 0`

### 10. Testing Strategy
- **Unit tests:** Verbatim flag handling, metadata inclusion
- **Integration tests:** Verbatim + all filter combinations
- **CLI tests:** --verbatim flag, output format
- **MCP tests:** verbatim parameter, response structure
- **As_prompt tests:** Budget enforcement with raw content

## Files to Create/Modify
- Modify: `src/lore/types.py` (RecallConfig, RecallResult)
- Modify: `src/lore/lore.py` (recall method signature, logic)
- Modify: `src/lore/cli.py` (--verbatim flag)
- Modify: `src/lore/mcp/server.py` (recall tool parameter)
- Modify: `src/lore/prompt/formatter.py` (as_prompt verbatim handling)
- Create: `tests/test_verbatim_recall.py`

## Dependencies
- No new external dependencies
- Uses existing recall pipeline
- Uses existing formatter

## Backward Compatibility
- ✅ Default `verbatim=False` preserves existing behavior
- ✅ No breaking changes to APIs
- ✅ All existing filters work unchanged
- ✅ Existing code unaffected
