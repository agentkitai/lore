# Lore Project Context (for BMAD Agents)

## What is Lore?
Lore is a cross-agent memory SDK — store, recall, and share knowledge across AI agents. It provides persistent semantic memory via MCP (Model Context Protocol), allowing any MCP-compatible AI tool (Claude, Cursor, VS Code, ChatGPT) to remember and recall knowledge.

## Current Version: v0.5.1
- **Storage:** SQLite (local) + Postgres via HttpStore (remote)
- **Embedding:** ONNX Runtime with dual embedding (code vs prose models)
- **Security:** PII masking + secret blocking pipeline
- **Freshness:** Git-based staleness detection
- **Feedback:** Upvote/downvote on memories
- **MCP Tools:** remember, recall, forget, list_memories, stats, upvote_memory, downvote_memory (7 tools)
- **Tests:** 590 passing

## Architecture
```
src/lore/
├── types.py          # Memory, RecallResult, MemoryStats dataclasses
├── lore.py           # Main Lore class (facade)
├── cli.py            # CLI (remember, recall, forget, memories, stats)
├── store/
│   ├── base.py       # Store ABC (save, get, list, update, delete, count, cleanup_expired)
│   ├── sqlite.py     # SQLite implementation
│   ├── memory.py     # In-memory store (testing)
│   └── http.py       # HttpStore (Postgres backend via REST API)
├── embed/            # Embedding pipeline (dual model: code vs prose)
├── mcp/
│   └── server.py     # MCP server (7 tools)
├── redact/           # PII masking + secret blocking
├── freshness/        # Git-based staleness detection
├── server/           # HTTP server (Postgres + pgvector backend)
└── github/           # GitHub sync integration
```

## Memory Data Model (types.py)
```python
@dataclass
class Memory:
    id: str
    content: str
    type: str = "general"          # general, code, note, lesson, convention, fact, preference, debug, pattern
    context: Optional[str] = None
    tags: List[str] = []
    metadata: Optional[Dict[str, Any]] = None
    source: Optional[str] = None
    project: Optional[str] = None
    embedding: Optional[bytes] = None
    created_at: str = ""
    updated_at: str = ""
    ttl: Optional[int] = None
    expires_at: Optional[str] = None
    confidence: float = 1.0
    upvotes: int = 0
    downvotes: int = 0
```

## Existing Decay System
- Type-specific decay half-lives: code=14d, note=21d, lesson=30d, convention=60d
- General type uses global default (30 days)
- Semantic decay scoring adjusts recall relevance based on age

## Store ABC Interface
```python
class Store(ABC):
    save(memory) -> None
    get(memory_id) -> Optional[Memory]
    list(project, type, limit) -> List[Memory]
    update(memory) -> bool
    delete(memory_id) -> bool
    count(project, type) -> int
    cleanup_expired() -> int
```

## Server API (Postgres backend, localhost:8765)
- POST /api/v1/memories — create memory (expects pre-computed embedding)
- GET /api/v1/memories/:id — get by ID
- GET /api/v1/memories — list with filters
- PUT /api/v1/memories/:id — update
- DELETE /api/v1/memories/:id — delete
- POST /api/v1/memories/search — vector similarity search
- GET /api/v1/stats — aggregate statistics

## v0.6.0 Target
We're building 10 new features across 5 phases to transform Lore into a full cognitive memory platform. See `_bmad/planning/v0.6.0-roadmap.md` for the full plan.

## Tech Stack
- Python 3.9+ (SDK + CLI + MCP server)
- SQLite + Postgres/pgvector (storage)
- ONNX Runtime (local embeddings, no API key needed)
- FastMCP (MCP server framework)
- Docker (Postgres server deployment)
- pytest (testing, 590 tests)

## Key Principle
All LLM-powered features must be OPTIONAL. Lore works without any LLM API key — just local embeddings. LLM features (enrichment, classification, fact extraction) are opt-in via config.
