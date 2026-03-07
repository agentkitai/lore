# F3: Temporal Recall Filters — Architecture Document

## Overview
Add flexible date-range filtering to recall queries via SQL WHERE clause extension.

## Components

### 1. RecallConfig Extended with Temporal Filters
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
    # NEW - Temporal filters:
    date_from: Optional[str] = None  # ISO 8601
    date_to: Optional[str] = None    # ISO 8601
    before: Optional[str] = None     # ISO 8601 timestamp
    after: Optional[str] = None      # ISO 8601 timestamp
    year: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None
    days_ago: Optional[int] = None
    hours_ago: Optional[int] = None
    window: Optional[str] = None     # 'last_week', 'last_month', etc.
```

### 2. Temporal Filter Resolution
File: `src/lore/temporal.py` (new)
```python
class TemporalFilterResolver:
    PRESETS = {
        'today': (0, 0),           # days_ago range
        'last_hour': (0.04, 0.04), # ~1 hour in days
        'last_day': (1, 1),
        'last_week': (7, 7),
        'last_month': (30, 30),
        'last_year': (365, 365),
    }
    
    @staticmethod
    def resolve(config: RecallConfig) -> Tuple[Optional[datetime], Optional[datetime]]:
        """Convert temporal params to date_from/date_to range."""
        
        # Priority: explicit dates > relative times > presets
        if config.window:
            days = TemporalFilterResolver.PRESETS[config.window][0]
            date_from = datetime.now() - timedelta(days=days)
            return (date_from, datetime.now())
        
        if config.date_from and config.date_to:
            return (parse_iso(config.date_from), parse_iso(config.date_to))
        
        if config.days_ago is not None:
            date_from = datetime.now() - timedelta(days=config.days_ago)
            return (date_from, datetime.now())
        
        if config.year and config.month and config.day:
            dt = datetime(config.year, config.month, config.day)
            return (dt, dt + timedelta(days=1))
        
        if config.year and config.month:
            date_from = datetime(config.year, config.month, 1)
            next_month = date_from + timedelta(days=32)
            date_to = datetime(next_month.year, next_month.month, 1)
            return (date_from, date_to)
        
        # ... handle other combinations
        return (None, None)
```

### 3. SQL WHERE Clause Extension
File: `src/lore/store/sqlite.py`
```python
def _build_temporal_where(self, config: RecallConfig) -> Tuple[str, List]:
    """Build temporal WHERE conditions."""
    where_parts = []
    params = []
    
    date_from, date_to = TemporalFilterResolver.resolve(config)
    
    if date_from and date_to:
        where_parts.append("created_at BETWEEN ? AND ?")
        params.extend([date_from, date_to])
    elif date_from:
        where_parts.append("created_at >= ?")
        params.append(date_from)
    elif date_to:
        where_parts.append("created_at <= ?")
        params.append(date_to)
    
    if config.before:
        where_parts.append("created_at < ?")
        params.append(parse_iso(config.before))
    
    if config.after:
        where_parts.append("created_at >= ?")
        params.append(parse_iso(config.after))
    
    return (" AND ".join(where_parts), params) if where_parts else ("", [])

# Then in recall() method:
temporal_where, temporal_params = self._build_temporal_where(config)

query = f"""
SELECT * FROM memories
WHERE {semantic_where}
  AND {project_where}
  AND {tier_where}
  AND {type_where}
  AND {metadata_where}
  {f'AND {temporal_where}' if temporal_where else ''}
ORDER BY importance_score DESC
LIMIT ? OFFSET ?
"""
```

### 4. CLI Integration
File: `src/lore/cli.py`
```python
def cmd_recall(args):
    results = lore.recall(
        args.query,
        year=args.year,
        month=args.month,
        day=args.day,
        days_ago=args.days_ago,
        hours_ago=args.hours_ago,
        window=args.window,
        before=args.before,
        after=args.after,
        date_from=args.date_from,
        date_to=args.date_to,
        ...
    )
    
# Usage:
# lore recall "python" --year 2024 --month 3
# lore recall "python" --days-ago 7
# lore recall "python" --window last_month
# lore recall "python" --before 2024-12-31T00:00:00Z
```

### 5. MCP Tool Parameters
File: `src/lore/mcp/server.py`
```python
@mcp_server.tool()
async def recall(
    query: str,
    ...,
    # NEW temporal params:
    year: Optional[int] = None,
    month: Optional[int] = None,
    day: Optional[int] = None,
    days_ago: Optional[int] = None,
    hours_ago: Optional[int] = None,
    window: Optional[str] = None,  # 'last_week', 'last_month', etc.
    before: Optional[str] = None,
    after: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    """Retrieve memories with optional temporal filtering."""
```

### 6. SDK Method Signature
File: `src/lore/lore.py`
```python
async def recall(
    self,
    query: str,
    ...,
    # NEW temporal params:
    year: Optional[int] = None,
    month: Optional[int] = None,
    day: Optional[int] = None,
    days_ago: Optional[int] = None,
    hours_ago: Optional[int] = None,
    window: Optional[str] = None,
    before: Optional[str] = None,
    after: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[RecallResult]:
    """Retrieve memories with optional temporal filtering."""
```

### 7. Temporal + Existing Filters
- All temporal filters compose with project/tier/topic/sentiment/entity/category filters
- SQL WHERE builds from all conditions
- Example: `recall("python", year=2024, project="work", topic="learning")`

### 8. Importance Scoring Preserved
- ORDER BY still applies `importance_score DESC` after temporal filtering
- Tier weights still applied
- Works with as_prompt budget enforcement

### 9. Performance Considerations
- Use indexed `created_at` column
- All filtering in SQL WHERE (no post-filtering)
- Query planner optimizes BETWEEN on indexed column
- Date range filters reduce result set before semantic search

### 10. Testing Strategy
- **Unit tests:** Filter resolution (presets, date parsing, ranges)
- **Integration tests:** Temporal filters with all combinations
- **Edge cases:** Leap years, DST boundaries, timezone handling
- **CLI tests:** Flag parsing, help text
- **MCP tests:** Parameter validation, response structure

## Files to Create/Modify
- Modify: `src/lore/types.py` (RecallConfig extended)
- Create: `src/lore/temporal.py` (TemporalFilterResolver)
- Modify: `src/lore/lore.py` (recall signature + logic)
- Modify: `src/lore/store/sqlite.py` (temporal WHERE building)
- Modify: `src/lore/store/http.py` (if applicable)
- Modify: `src/lore/cli.py` (temporal flags)
- Modify: `src/lore/mcp/server.py` (temporal parameters)
- Create: `tests/test_temporal_filters.py`

## Dependencies
- No new external dependencies
- Uses Python `datetime` and `dateutil` (already required)

## Backward Compatibility
- ✅ All temporal params optional (None by default)
- ✅ Default behavior unchanged (no temporal filtering)
- ✅ No breaking changes to APIs
- ✅ Existing calls work unmodified
