"""Generate tests/persistence/fixtures/embeddings.json — run once, then commit.

Usage:
    python scripts/generate_embedding_fixtures.py
"""

import json
from pathlib import Path

from lore.embed.local import LocalEmbedder

STRINGS = [
    # Postgres / pgvector
    "pgvector cosine distance operator",
    "asyncpg connection pool sizing",
    "postgres jsonb GIN index performance",
    "pgvector ivfflat index approximate nearest neighbor",
    "postgres EXPLAIN ANALYZE query plan",
    "asyncpg fetchrow returns None when not found",
    "postgres transaction isolation level serializable",
    "pgvector half-precision vector storage",
    "postgres COPY FROM for bulk insert",
    "asyncpg prepared statement caching",
    # Python / FastAPI
    "fastapi dependency injection test fixture",
    "fastapi lifespan startup shutdown",
    "pydantic v2 model validator",
    "pytest asyncio fixture scope session",
    "python dataclass frozen slots comparison",
    "httpx async client testing fastapi",
    "python typing Protocol runtime_checkable",
    "uvicorn asgi server configuration",
    "python contextlib asynccontextmanager usage",
    "starlette request state dependency",
    # Embeddings / Vectors
    "vector embedding 384 dimensions miniLM",
    "knn nearest neighbor cosine similarity",
    "sentence transformers all-MiniLM-L6-v2",
    "ONNX runtime inference embedding model",
    "cosine similarity versus dot product vectors",
    "embedding dimensionality reduction PCA",
    "semantic search recall precision tradeoff",
    "vector index HNSW versus IVFFlat",
    "approximate nearest neighbor benchmarks",
    "normalized embedding L2 normalization",
    # Lore / memory system
    "lore memory persistence store protocol",
    "claude code mcp protocol memory tool",
    "memory importance score decay half-life",
    "upvote downvote memory relevance scoring",
    "memory expiry TTL automatic cleanup",
    "access count bump last accessed at",
    "org isolation multi-tenant memory store",
    "memory tags jsonb filter query",
    "recall by embedding ranked results",
    "snapshot save session context checkpoint",
    # Software engineering general
    "rate limit token bucket algorithm",
    "openai gpt-4o-mini cost per token",
    "ulid vs uuid comparison monotonic",
    "event sourcing CQRS architecture pattern",
    "circuit breaker retry exponential backoff",
    "dependency injection inversion of control",
    "twelve-factor app configuration environment",
    "hexagonal architecture ports adapters",
    "SOLID principles single responsibility",
    "eventual consistency distributed systems",
    # Testing
    "contract test parametrized fixture backend",
    "test driven development red green refactor",
    "pytest mark asyncio strict mode",
    "transaction rollback test isolation pattern",
    "mock versus stub versus fake test double",
    "property based testing hypothesis library",
    "mutation testing coverage quality",
    "integration test versus unit test boundary",
    "end to end test playwright browser",
    "golden file snapshot regression testing",
    # Infrastructure / DevOps
    "docker compose postgres pgvector service",
    "kubernetes horizontal pod autoscaling",
    "prometheus metrics scrape interval",
    "opentelemetry distributed tracing spans",
    "SLO error budget burn rate alert",
    "github actions CI CD workflow matrix",
    "terraform infrastructure as code state",
    "redis cache LRU eviction policy",
    "nginx reverse proxy load balancing",
    "cloudflare workers edge computing",
    # Data / ML
    "pandas dataframe groupby aggregation",
    "numpy array broadcasting operations",
    "scikit learn pipeline cross validation",
    "gradient descent learning rate schedule",
    "attention mechanism transformer architecture",
    "tokenizer byte pair encoding BPE",
    "fine-tuning LORA low-rank adaptation",
    "RAG retrieval augmented generation",
    "chain of thought prompting reasoning",
    "structured output JSON schema extraction",
    # Languages / tooling
    "rust ownership borrowing lifetime",
    "typescript strict null checks",
    "go channels goroutines concurrency",
    "bash heredoc multiline string",
    "jq JSON query filter transformation",
    "git bisect binary search regression",
    "ripgrep fast regex search codebase",
    "make target phony dependency",
    "pre-commit hooks linting formatting",
    "semantic versioning breaking change minor",
    # Miscellaneous varied
    "timezone aware datetime UTC conversion",
    "ULID sortable unique identifier generation",
    "JSON schema validation draft 2020",
    "websocket connection upgrade handshake",
    "server sent events streaming response",
    "content security policy XSS prevention",
    "argon2 password hashing salt rounds",
    "JWT token expiry refresh rotation",
    "OAuth2 PKCE authorization code flow",
    "HTTP/2 multiplexing header compression",
]

assert len(STRINGS) == 100, f"Expected 100 strings, got {len(STRINGS)}"
# Deduplicate preserving order
seen: set[str] = set()
deduped = []
for s in STRINGS:
    if s not in seen:
        seen.add(s)
        deduped.append(s)
assert len(deduped) == 100, f"Duplicates found; unique count={len(deduped)}"

print("Loading LocalEmbedder (may download model on first run, ~10s)...")
emb = LocalEmbedder()
print(f"Embedding {len(deduped)} strings...")
out = []
for i, s in enumerate(deduped):
    v = emb.embed(s)
    out.append({"text": s, "embedding": list(v)})
    if (i + 1) % 10 == 0:
        print(f"  {i + 1}/{len(deduped)} done")

fixture_path = (
    Path(__file__).parent.parent / "tests" / "persistence" / "fixtures" / "embeddings.json"
)
fixture_path.parent.mkdir(parents=True, exist_ok=True)
fixture_path.write_text(json.dumps(out, indent=2))
print(f"Wrote {len(out)} fixtures to {fixture_path}")
