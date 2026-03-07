# F1: On This Day — Architecture Document

## Overview
Add `OnThisDayEngine` to enable temporal memory retrieval by month+day across all years.

## Components

### 1. OnThisDayEngine Class
Located in `src/lore/temporal.py`:
```python
class OnThisDayEngine:
    def __init__(self, store: Store, logger):
        self.store = store
        self.logger = logger
    
    async def on_this_day(
        self,
        month: Optional[int] = None,
        day: Optional[int] = None,
        project: Optional[str] = None,
        tier: Optional[str] = None,
        date_window_days: int = 1,
        limit: Optional[int] = None,
        offset: int = 0
    ) -> Dict[int, List[Memory]]:  # {year: [memories...]}
        """Query memories from month+day across all years, grouped by year."""
```

### 2. SQL Query Pattern
```sql
SELECT *
FROM memories
WHERE 
  EXTRACT(month FROM created_at) = ?
  AND EXTRACT(day FROM created_at) BETWEEN ? AND ?
  AND (? IS NULL OR project = ?)
  AND (? IS NULL OR tier = ?)
  AND valid_until IS NULL
  AND archived IS NULL
ORDER BY
  EXTRACT(year FROM created_at) DESC,
  importance_score DESC,
  created_at DESC
```

### 3. Results Grouping (Python)
```python
# After query, group by year
results_by_year = {}
for memory in memories:
    year = memory.created_at.year
    if year not in results_by_year:
        results_by_year[year] = []
    results_by_year[year].append(memory)

return results_by_year  # {2024: [...], 2023: [...], ...}
```

### 4. CLI Integration
File: `src/lore/cli.py`
- New subcommand: `cmd_on_this_day(args)`
- Flags: `--month`, `--day`, `--project`, `--tier`, `--limit`, `--offset`, `--json`
- Usage: `lore on-this-day --month 3 --day 6 --project work`

### 5. MCP Tool Interface
File: `src/lore/mcp/server.py`
```python
@mcp_server.tool()
async def on_this_day(
    month: Optional[int] = None,
    day: Optional[int] = None,
    project: Optional[str] = None,
    tier: Optional[str] = None,
    limit: Optional[int] = None
) -> Dict[str, Any]:
    """Retrieve memories from this month+day across all years."""
```

### 6. SDK Method Signature
File: `src/lore/lore.py`
```python
async def on_this_day(
    self,
    month: Optional[int] = None,
    day: Optional[int] = None,
    project: Optional[str] = None,
    tier: Optional[str] = None,
    date_window_days: int = 1,
    limit: Optional[int] = None
) -> Dict[int, List[Memory]]:
    """Query memories from this month+day across all years."""
    return await self._temporal_engine.on_this_day(...)
```

### 7. Tier Visibility Integration
- Respect `valid_until` on all memories (expired tier memories excluded)
- Apply tier-based `importance_score` weighting during query
- Filter by `tier` parameter if provided
- Include archived status check

### 8. Importance Scoring Within Years
- ORDER BY `importance_score DESC` within each year group
- Importance already computed as multiplicative score (access × time decay × tier weight)
- No additional computation needed

### 9. Date Window Configuration
- Default: `date_window_days=1` (match day ± 1)
- SQL: `EXTRACT(day FROM created_at) BETWEEN ? AND ?`
- Example: month=3, day=6, window=1 → days 5-7

### 10. Testing Strategy
- **Unit tests:** Query building, grouping logic, edge cases (leap year, etc.)
- **Integration tests:** Full pipeline with real data, tier filtering, importance ordering
- **CLI tests:** Argument parsing, output formatting
- **MCP tests:** Tool call, response structure

## Files to Create/Modify
- Create: `src/lore/temporal.py` (OnThisDayEngine)
- Modify: `src/lore/lore.py` (add on_this_day SDK method)
- Modify: `src/lore/cli.py` (add CLI command)
- Modify: `src/lore/mcp/server.py` (add MCP tool)
- Create: `tests/test_temporal.py` (all tests)

## Dependencies
- No new external dependencies
- Uses existing Store abstraction
- Uses existing importance scoring
- Uses existing tier visibility

## Backward Compatibility
- ✅ No changes to existing APIs
- ✅ New feature only
- ✅ No schema changes required
