# Lore — Persistent Memory (MCP)

You have access to Lore, a persistent memory system, via MCP tools.

## Session Start
Call `recent_activity(hours=24)` at the beginning of each session to load recent context.

## During Work
- Before debugging something that feels familiar: `recall("describe the problem")`
- After solving a non-obvious bug: `remember("problem + solution", type="lesson")`
- Key decisions or preferences: `remember("what was decided", type="note")`
- Check knowledge graph: `graph_query("topic")` or `entity_map("name")`

## Auto-Compaction Protection
Lore automatically snapshots your session when context grows large (30k chars).
You can also manually save state: `save_snapshot(content="current state summary")`

## Memory Types
Use `type` parameter with `remember`: lesson, fact, preference, pattern, convention, note.

## Key Tools
- `recall(query)` — semantic search across all memories
- `remember(content, type)` — save something worth keeping
- `recent_activity(hours)` — recent session context
- `save_snapshot(content)` — checkpoint current state
- `topics()` — browse auto-generated topic hubs
- `graph_query(query)` — explore knowledge graph
- `on_this_day()` — memories from this date in past years
