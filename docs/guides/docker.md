# Docker Deployment Guide

## Quick Start

```bash
git clone https://github.com/amitpaz1/lore.git && cd lore
docker compose up -d
```

Server is running at `http://localhost:8765`.

## Architecture

The Docker stack includes:

```
┌─────────────────────────────────────┐
│        Lore Server (:8765)          │
│   FastAPI + ONNX Embedding Model    │
│         (Python 3.11-slim)          │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│    PostgreSQL 16 + pgvector (:5432) │
│         (pgvector/pgvector:pg16)    │
│         Volume: pgdata              │
└─────────────────────────────────────┘
```

## Development Setup

Uses hot-reload for rapid iteration:

```bash
docker compose up -d
```

Features:
- Source code mounted as volume (changes reflected immediately)
- `--reload` flag on uvicorn
- PostgreSQL accessible at `localhost:5432`

## Production Setup

### Step 1: Configure

```bash
# Generate secure password
echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)" > .env
```

### Step 2: Start

```bash
docker compose -f docker-compose.prod.yml up -d
```

### Step 3: Initialize

```bash
curl -s -X POST http://localhost:8765/v1/org/init \
  -H "Content-Type: application/json" \
  -d '{"name": "my-org"}'
```

### Production features:

- Multi-stage Dockerfile (smaller image, no build tools)
- Non-root user (`lore`)
- Health checks
- Memory limits (512 MB)
- Auto-restart on failure

## Dockerfile Details

The `Dockerfile.server` uses a two-stage build:

1. **Builder stage**: Installs dependencies with gcc/libpq-dev
2. **Runtime stage**: Copies only compiled packages + runtime libs

Result: smaller image, no compiler toolchain in production.

## Docker Compose Files

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Development (hot reload, source mounted) |
| `docker-compose.prod.yml` | Production (resource limits, restart policy) |

## Useful Commands

```bash
# View logs
docker compose logs -f lore

# Check health
curl http://localhost:8765/health

# Restart server only
docker compose restart lore

# Full rebuild
docker compose build --no-cache && docker compose up -d

# Stop everything
docker compose down

# Stop and remove data
docker compose down -v
```

## Custom Configuration

Override environment variables in docker-compose:

```yaml
services:
  lore:
    environment:
      DATABASE_URL: postgresql://lore:secret@db:5432/lore
      LORE_REDACT: "true"
```

## Resource Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| Lore Server | 256 MB RAM | 512 MB RAM |
| PostgreSQL | 256 MB RAM | 512 MB RAM |
| Disk | 1 GB | 10 GB+ |
