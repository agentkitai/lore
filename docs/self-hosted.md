# Self-Hosted Setup

Run Lore Cloud on your own infrastructure with Docker Compose.

## Prerequisites

- Docker & Docker Compose v2+
- 512 MB RAM minimum

## Quick Start

```bash
git clone https://github.com/amitpaz1/lore.git
cd lore

# Set a real password for production
echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)" > .env

docker compose -f docker-compose.prod.yml up -d
```

Server is now running at `http://localhost:8765`. Migrations run automatically on startup.

## Initialize Your Organization

```bash
curl -s -X POST http://localhost:8765/v1/org/init \
  -H "Content-Type: application/json" \
  -d '{"name": "my-org"}' | jq
```

Save the returned `api_key` — it's shown only once.

## Create a Project-Scoped Key

```bash
curl -s -X POST http://localhost:8765/v1/keys \
  -H "Authorization: Bearer $ROOT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-project", "project": "my-project"}' | jq
```

## Connect the SDK

```python
from lore import Lore

lore = Lore(
    store="remote",
    api_url="http://localhost:8765",
    api_key="lore_sk_...",
)

lore.publish(problem="...", resolution="...")
lessons = lore.query("my problem")
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | (set in compose) | PostgreSQL connection string |
| `POSTGRES_PASSWORD` | `lore` | DB password (set in `.env` or export) |

## Persistence

Data is stored in the `pgdata` Docker volume. Back it up with:

```bash
docker compose -f docker-compose.prod.yml exec db pg_dump -U lore lore > backup.sql
```

## Updating

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

Migrations are idempotent and run on every startup.
