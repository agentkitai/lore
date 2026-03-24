# Environment Variables

Complete reference for all environment variables used by Lore. Variables are grouped by category.

---

## Core

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `LORE_STORE` | `local` | No | Storage backend: `local` (SQLite) or `remote` (HTTP API) |
| `LORE_PROJECT` | none | No | Default project namespace for all operations |
| `LORE_API_URL` | none | Yes (remote mode) | Server URL when using remote store |
| `LORE_API_KEY` | none | Yes (remote mode) | API key for authenticating with the remote server |
| `LORE_HTTP_TIMEOUT` | none | No | HTTP request timeout in seconds for the remote store client |

---

## LLM

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `LORE_LLM_PROVIDER` | none | No | LLM provider: `anthropic`, `openai`, `azure`, etc. |
| `LORE_LLM_MODEL` | `gpt-4o-mini` | No | Model for classification, extraction, and consolidation |
| `LORE_LLM_API_KEY` | none | No | API key for the configured LLM provider |
| `LORE_LLM_BASE_URL` | none | No | Custom base URL for the LLM API (e.g., for proxies or self-hosted models) |

---

## Features

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `LORE_ENRICHMENT_ENABLED` | `false` | No | Enable LLM-powered enrichment on memory creation |
| `LORE_ENRICHMENT_MODEL` | `gpt-4o-mini` | No | Model used for the enrichment pipeline |
| `LORE_CLASSIFY` | `false` | No | Enable intent/domain/emotion classification on remember |
| `LORE_FACT_EXTRACTION` | `false` | No | Enable automatic fact triple extraction on remember |
| `LORE_KNOWLEDGE_GRAPH` | `false` | No | Enable knowledge graph entity and relationship tracking |
| `LORE_SNAPSHOT_THRESHOLD` | `30000` | No | Character threshold for automatic session snapshots (MCP server) |

---

## Knowledge Graph Tuning

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `LORE_GRAPH_DEPTH` | `0` | No | Default graph traversal depth during recall |
| `LORE_GRAPH_MAX_DEPTH` | none | No | Maximum allowed graph traversal depth |
| `LORE_GRAPH_CONFIDENCE_THRESHOLD` | `0.5` | No | Minimum confidence score for graph entities |
| `LORE_GRAPH_CO_OCCURRENCE` | `true` | No | Extract co-occurrence relationships between entities |
| `LORE_GRAPH_CO_OCCURRENCE_WEIGHT` | `0.3` | No | Default weight for co-occurrence edges |

---

## Database

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `DATABASE_URL` | none | Yes (server) | PostgreSQL connection string (e.g., `postgresql://user:pass@host:5432/db`) |
| `REDIS_URL` | none | No | Redis connection string for rate limiting (e.g., `redis://localhost:6379/0`) |
| `MIGRATIONS_DIR` | `migrations` | No | Path to SQL migration files |

---

## Server

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `HOST` | `0.0.0.0` | No | Server bind address |
| `PORT` | `8765` | No | Server listen port |
| `AUTH_MODE` | `api-key-only` | No | Authentication mode: `api-key-only`, `dual`, or `oidc-required` |
| `METRICS_ENABLED` | `true` | No | Enable the `/metrics` Prometheus endpoint |
| `LOG_FORMAT` | `pretty` | No | Log output format: `pretty` or `json` |
| `LOG_LEVEL` | `INFO` | No | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

---

## Rate Limiting

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `RATE_LIMIT_BACKEND` | `memory` | No | Backend for rate limiting: `memory` or `redis` |
| `RATE_LIMIT_MAX` | `100` | No | Maximum requests per window per API key |
| `RATE_LIMIT_WINDOW` | `60` | No | Rate limit window in seconds |

---

## OIDC / JWT

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `OIDC_ISSUER` | none | No | OIDC issuer URL for JWT validation |
| `OIDC_AUDIENCE` | none | No | Expected JWT audience claim |
| `OIDC_ROLE_CLAIM` | `role` | No | JWT claim containing the user role |
| `OIDC_ORG_CLAIM` | `tenant_id` | No | JWT claim containing the organization/tenant ID |

---

## Alerting

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `SLO_CHECK_INTERVAL` | `60` | No | Interval in seconds between SLO health checks |
| `ALERT_WEBHOOK_URL` | none | No | Webhook URL for SLO alert notifications |
| `SMTP_HOST` | none | No | SMTP server hostname for email alerts |
| `SMTP_PORT` | `587` | No | SMTP server port |
| `SMTP_USER` | none | No | SMTP authentication username |
| `SMTP_PASS` | none | No | SMTP authentication password |
| `SMTP_FROM` | `SMTP_USER` | No | Sender address for alert emails |

---

## Secrets Management

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `AWS_SECRET_ARN` | none | No | AWS Secrets Manager ARN; secrets are loaded into env at startup |
| `*_FILE` | none | No | Docker secret path pattern (e.g., `DATABASE_URL_FILE=/run/secrets/db_url`) |
