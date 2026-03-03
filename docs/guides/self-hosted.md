# Self-Hosted Deployment Guide

Run Lore on your own infrastructure with Docker Compose.

## Prerequisites

- Docker & Docker Compose v2+
- 512 MB RAM minimum (1 GB recommended)

## Quick Start (Development)

```bash
git clone https://github.com/amitpaz1/lore.git && cd lore
docker compose up -d
```

This starts:
- **Lore server** on port 8765 (with hot reload)
- **PostgreSQL 16 + pgvector** on port 5432

## Production Deployment

### Step 1: Clone and Configure

```bash
git clone https://github.com/amitpaz1/lore.git && cd lore

# Generate a secure database password
echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)" > .env
```

### Step 2: Start Services

```bash
docker compose -f docker-compose.prod.yml up -d
```

### Step 3: Initialize Organization

```bash
curl -s -X POST http://localhost:8765/v1/org/init \
  -H "Content-Type: application/json" \
  -d '{"name": "my-org"}' | python3 -m json.tool
```

Save the returned `api_key` -- it's shown only once.

### Step 4: Verify

```bash
# Health check
curl http://localhost:8765/health

# Create a test memory
curl -X POST http://localhost:8765/v1/memories \
  -H "Authorization: Bearer lore_sk_your_key" \
  -H "Content-Type: application/json" \
  -d '{"content": "Hello from Lore!", "type": "note"}'
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | (set in compose) | PostgreSQL connection string |
| `POSTGRES_PASSWORD` | `lore` | DB password (set in `.env`) |
| `LORE_STORE` | `local` | `local` (SQLite) or `remote` (HTTP) |
| `LORE_PROJECT` | -- | Default project scope |
| `LORE_API_URL` | -- | Server URL (remote mode) |
| `LORE_API_KEY` | -- | API key (remote mode) |
| `LORE_MODEL_DIR` | `~/.lore/models` | Embedding model cache |
| `LORE_REDACT` | `false` | Enable PII redaction |

## Backup and Restore

### Backup

```bash
docker compose exec db pg_dump -U lore lore > backup.sql
```

### Restore

```bash
docker compose exec -T db psql -U lore lore < backup.sql
```

## Updating

```bash
git pull
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
```

Migrations are idempotent and run on every startup.

## Reverse Proxy (nginx)

Example nginx config for HTTPS:

```nginx
server {
    listen 443 ssl;
    server_name lore.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/lore.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/lore.yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://localhost:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Monitoring

- Health endpoint: `GET /health`
- Stats endpoint: `GET /v1/stats` (requires API key)
- Docker logs: `docker compose logs -f lore`
