# Contributing to Lore

Thanks for your interest in contributing! Lore is an open-source universal AI memory layer, and we welcome contributions of all kinds.

## Getting Started

### Prerequisites

- Python 3.9+
- Node 18+ (for TypeScript SDK)
- Docker & Docker Compose (for server development)

### Development Setup

```bash
git clone https://github.com/amitpaz1/lore.git && cd lore

# Python SDK + tests
pip install -e ".[dev,server,mcp,cli]"
pytest

# TypeScript SDK
cd ts && npm install && npm test
```

### Running Tests

```bash
# Python tests (497 tests)
pytest

# Python tests with coverage
pytest --cov=lore

# Skip integration tests (no Docker needed)
pytest -m "not integration"

# TypeScript tests
cd ts && npm test
```

### Running the Server Locally

```bash
docker compose up -d
```

## How to Contribute

### Reporting Bugs

Open an issue with:
- What you expected to happen
- What actually happened
- Steps to reproduce
- Your environment (OS, Python version, etc.)

### Suggesting Features

Open an issue describing:
- The problem you're trying to solve
- Your proposed solution
- Any alternatives you considered

### Pull Requests

1. Fork the repo and create a branch from `main`
2. Add tests for any new functionality
3. Ensure all tests pass (`pytest` and `npm test`)
4. Update documentation if needed
5. Submit a PR with a clear description

### Code Style

**Python:**
- We use [Ruff](https://github.com/astral-sh/ruff) for linting
- Line length: 180 characters
- Target: Python 3.9+
- Run `ruff check src/` before committing

**TypeScript:**
- ESM modules
- Strict TypeScript
- Run `npm run lint` in the `ts/` directory

### Commit Messages

Use conventional commit format:

```
feat: add memory expiration support
fix: handle empty tags in recall
docs: update CLI usage guide
test: add TTL integration tests
```

## Project Structure

```
lore/
├── src/lore/           # Python SDK
│   ├── mcp/            # MCP server
│   ├── server/         # REST API server
│   ├── memory_store/   # Memory store implementations
│   ├── embed/          # Embedding pipeline
│   └── redact/         # PII redaction
├── ts/                 # TypeScript SDK
│   ├── src/
│   └── tests/
├── tests/              # Python tests
├── migrations/         # SQL migrations
├── docs/               # Documentation
├── examples/           # Config examples
└── docker-compose.yml  # Docker setup
```

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
