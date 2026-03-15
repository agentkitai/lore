# Lore — Universal AI Memory

You have access to **Lore**, a persistent memory system via MCP tools.
Use it to remember context across sessions and recall relevant knowledge.

## When to Use Lore

### Session Start
Call `recent_activity(hours=24)` to load what happened recently.
This gives you grouped context from the last day — decisions, lessons, work done.

### During Work
- **Before debugging**: `recall("describe the problem")` — check if this was solved before
- **After solving something non-obvious**: `remember("what you learned", type="lesson")`
- **Key decisions**: `remember("decision and reasoning", type="note")`
- **Preferences discovered**: `remember("user prefers X", type="preference")`

### Pre-Compaction (Automatic)
Lore's session accumulator auto-saves snapshots when your context grows large.
You don't need to manually call `save_snapshot` — but you can if you want to
preserve specific state before a complex transition:
```
save_snapshot(content="Current state: ...", title="mid-refactor checkpoint")
```

## Key Tools

| Tool | When |
|------|------|
| `recall(query)` | Search memories semantically |
| `remember(content, type)` | Save a memory (types: note, lesson, fact, preference, pattern, convention) |
| `recent_activity(hours)` | Load recent session context |
| `save_snapshot(content)` | Manually checkpoint current state |
| `topics()` | Browse auto-generated topic summaries |
| `graph_query(query)` | Explore knowledge graph connections |
| `entity_map(name)` | Find everything related to an entity |
| `on_this_day()` | Memories from this date in prior years |
| `export(format)` | Export all data (json/markdown) |

## Types for `remember`

- `lesson` — Bug fixes, gotchas, things learned the hard way
- `fact` — Objective information (API endpoints, config values, specs)
- `preference` — User preferences, style choices
- `pattern` — Recurring patterns or anti-patterns
- `convention` — Project conventions, naming rules
- `note` — General notes, decisions, context

## Don't Overthink It

If something seems worth remembering, `remember` it. If you're stuck, `recall` it.
The system handles deduplication, scoring, and cleanup automatically.
