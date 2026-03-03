# CLI Usage Guide

Lore includes a command-line interface for managing memories from your terminal.

## Install

```bash
pip install lore-sdk[cli]
```

## Commands

### `lore remember` -- Store a memory

```bash
lore remember "Stripe rate-limits at 100 req/min" --type lesson --tags stripe,api
```

Options:
| Flag | Description |
|------|-------------|
| `--type` | Memory type (note, lesson, snippet, fact, etc.) |
| `--tags` | Comma-separated tags |
| `--source` | Source identifier |
| `--ttl` | Time to live (e.g., `7d`, `1h`, `30m`) |
| `--project` | Project scope |
| `--json` | Output as JSON |

### `lore recall` -- Search memories

```bash
lore recall "rate limiting" --limit 5
```

Options:
| Flag | Description |
|------|-------------|
| `--type` | Filter by type |
| `--limit` | Max results (default: 5) |
| `--project` | Filter by project |
| `--json` | Output as JSON |

### `lore forget` -- Delete memories

```bash
# Delete by ID
lore forget 01HXYZ...

# Delete by tags
lore forget --tags outdated
```

### `lore memories` -- List memories

```bash
lore memories --type lesson --limit 10
```

Options:
| Flag | Description |
|------|-------------|
| `--type` | Filter by type |
| `--tags` | Filter by tags |
| `--limit` | Max results (default: 20) |
| `--offset` | Pagination offset |
| `--project` | Filter by project |
| `--include-expired` | Include expired memories |
| `--json` | Output as JSON |

### `lore stats` -- View statistics

```bash
lore stats
```

```
Memory Store Statistics
  Total memories: 42
  By type:
    lesson: 25
    note: 12
    snippet: 5
  By project:
    backend: 20
    frontend: 15
    shared: 7
  Oldest: 2025-01-15T10:30:00+00:00
  Newest: 2026-03-03T14:20:00+00:00
```

## Common Options

These flags work with all commands:

| Flag | Description |
|------|-------------|
| `--db` | SQLite database path (default: `~/.lore/default.db`) |
| `--project` | Project scope |
| `--json` | Machine-readable JSON output |

## Examples

```bash
# Store a debugging insight
lore remember "PostgreSQL EXPLAIN ANALYZE shows seq scan on users table — add index on email column" \
  --type lesson --tags postgres,performance

# Find what you know about Docker
lore recall "docker deployment issues"

# List all lessons tagged with 'api'
lore memories --type lesson --tags api

# Store a temporary note that expires in 7 days
lore remember "Sprint review is Friday at 3pm" --type note --ttl 7d

# Get stats in JSON format
lore stats --json
```

## Configuration

The CLI reads configuration from environment variables:

```bash
export LORE_PROJECT="my-project"
export LORE_DB_PATH="~/.lore/custom.db"
```

Or create `~/.lore/config.yaml`:

```yaml
project: my-project
db_path: ~/.lore/default.db
```
