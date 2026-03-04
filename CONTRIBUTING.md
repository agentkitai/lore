# Contributing to Lore

## Development Setup

### Python SDK

```bash
# Clone the repo
git clone https://github.com/amitpaz1/lore.git
cd lore

# Install in editable mode with dev dependencies
pip install -e ".[dev]"

# Run tests
python3 -m pytest -x -q

# Lint
ruff check src/ tests/
```

### TypeScript SDK

```bash
cd ts

# Install dependencies
npm install

# Run tests
npm test

# Build
npm run build

# Lint
npm run lint
```

## Project Structure

```
src/lore/          Python SDK
  lore.py          Main Lore class
  types.py         Memory, RecallResult, MemoryStats
  store/           Storage backends (sqlite, memory, remote)
  mcp/             MCP server integration
  cli.py           CLI entry point

ts/src/            TypeScript SDK (mirrors Python API)
  lore.ts          Main Lore class
  types.ts         Memory, RecallResult, MemoryStats
  store/           Storage backends

tests/             Python tests
ts/tests/          TypeScript tests
```

## Running Tests

All tests must pass before submitting a PR:

```bash
# Python
python3 -m pytest -x -q

# TypeScript
cd ts && npm test
```

## Code Style

- **Python:** Ruff with `line-length = 180`. Run `ruff check --fix src/ tests/`.
- **TypeScript:** ESLint. Run `npm run lint` in `ts/`.

## Commit Messages

Use conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
