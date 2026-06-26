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

## Redaction (write-side)

Write-side redaction masks/blocks secrets & PII at the create chokepoint. **Named
compliance policy packs (#80)** select scan levels + the default secrets action;
**L1 (zero-dep regex) is the default**, L2 (detect-secrets) and L3 (NER/spaCy)
activate only when their optional deps are installed (a level no-ops if its dep
is absent).

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `LORE_REDACT_POLICY` | _(L1)_ | No | Named pack: `off` \| `default`/`l1` (regex, mask secrets) \| `pii` (regex+NER, mask) \| `secrets` (regex+detect-secrets, block) \| `strict`/`all` (all levels, block). |
| `LORE_REDACT_LEVELS` | `1` | No | Explicit CSV of scan levels (e.g. `1,2,3`); overrides the pack's levels. |
| `LORE_REDACT_BLOCK` | _(unset)_ | No | Force BLOCK on detected secrets (default: mask + tag). |
| `LORE_REDACT_DENYLIST` | _(none)_ | No | Path to a denylist file (literal terms, or `re:`-prefixed regexes). |
| `LORE_REDACT_DISABLED` | _(unset)_ | No | Disable write-side redaction entirely. |

---

## Write-time reconciliation

On each write, a memory is reconciled against **the writer's own** existing memories in the same org/scope: a redundant near-duplicate is skipped, one that gains new tags is merged, a strong-but-changed prior version is superseded by the fresh one, otherwise it's a new row. Heuristic-only (cosine similarity, same-type, non-superseded candidates). It never touches another user's rows (a near-duplicate of a teammate's shared memory just becomes a new row), and `observation`-type memories always append. Set `LORE_RECONCILIATION_ENABLED=false` to restore pure append-only.

---

## Contradiction detection (write-time)

Where reconciliation folds near-duplicates, **contradiction detection** flags the
near-duplicates that *disagree* (#84). When enabled, a fire-and-forget task
LLM-scores the new memory against its similar neighbours **that the writer may see**
(migration-026 visibility — never another principal's private memory); a memory
that contradicts one gets a `contradiction` tag + `meta.contradicts` (ids, owners,
`cross_agent`, reason). Review via `list_memories(tags=["contradiction"])`. OFF by
default; a failure never blocks a write.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `LORE_CONTRADICTION_DETECTION` | `false` | No | Enable write-time contradiction flagging. |
| `LORE_CONTRADICTION_MIN_CONFIDENCE` | `0.6` | No | Min LLM confidence (0–1) to flag a contradiction. |
| `LORE_CONTRADICTION_MODEL` | _(LORE_ENRICHMENT_MODEL)_ | No | Model for contradiction scoring. |
| `LORE_CONTRADICTION_CONCURRENCY` | `4` | No | Max concurrent detection tasks (LLM fan-out cap). |

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `LORE_RECONCILIATION_ENABLED` | `true` | No | Reconcile writes (Add/Update/Delete/None) instead of always appending |
| `LORE_RECON_DUPLICATE_THRESHOLD` | `0.97` | No | Cosine ≥ this (same type, own) → near-exact: merge tags or skip |
| `LORE_RECON_SUPERSEDE_THRESHOLD` | `0.90` | No | Cosine in [this, duplicate) (same type, own) → supersede the prior version |
| `LORE_RECON_MAX_CANDIDATES` | `5` | No | Number of nearest candidates considered per write |

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

## Agent Identity (AgentGate)

When `AGENTGATE_JWT_SECRET` is set to AgentGate's signing secret, Lore accepts an
AgentGate-minted **agent token** (an `HS256` JWT with a `typ:"agent"` claim,
issued by `POST /api/agents/token`) as a Bearer credential. The memory is then
bound to the verified `agt_*` agent id as its owner (`principal_id`) — the same
identity string AgentLens stamps on traces, so an agent's memories and traces
correlate. The agent is a first-class principal: the existing private/shared
visibility rules apply unchanged (own private + team shared, private-by-default).

This is a distinct trust anchor, independent of `AUTH_MODE` (it works even in
`api-key-only` and `oidc-required` mode — setting a mode does **not** disable
it; leaving the secret unset does). When unset, the feature is off.
Verification is cryptographic only (no callback to AgentGate); agent tokens are
short-lived, which bounds revocation staleness.

> ⚠️ **Blast radius.** `AGENTGATE_JWT_SECRET` is the same HS256 secret AgentGate
> signs every agent token with, shared verbatim with AgentLens and Lore. Anyone
> who learns it can **forge a valid agent token for any `agt_*` id (and any
> `tid`/org)** accepted by all three services. Treat it as a master credential:
> store it in a secrets manager (or `*_FILE`), never commit it, rotate on
> suspicion, and only set it on Lore instances that should trust AgentGate
> agents. A future asymmetric/JWKS option would remove this shared-secret
> coupling.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `AGENTGATE_JWT_SECRET` | none | No | AgentGate's HS256 signing secret, shared with Lore to verify agent tokens. Unset → feature off. |

---

## Trust-aware recall (#79)

Provenance signal on recall: memories with no owning principal (`user_id IS NULL`)
are lower-provenance. **No-op by default.** This is a forensics/trust signal,
**not** a poisoning defense — a poisoned write from a legitimately authenticated
principal still ranks normally.

> ⚠️ **Only enable in multi-principal deployments.** "Unowned" is the *dominant*
> population in many setups: **solo / embedded mode writes every memory unowned**
> (so `LORE_RECALL_QUARANTINE_ANON=true` empties recall, and `ANON_WEIGHT<1`
> uniformly down-weights everything), and org-level background memories
> (consolidation / dreams / auto-extraction) are unowned **by design**. Use this
> only when agents/users write with a verified principal (OIDC / AgentGate token)
> and you specifically want to distinguish attributed from unattributed writes.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `LORE_RECALL_ANON_WEIGHT` | `1.0` | No | Score multiplier (0..1) for anonymous (unowned) memories on recall. `1.0` = no change; lower = down-weight. |
| `LORE_RECALL_QUARANTINE_ANON` | `false` | No | When true, drop anonymous memories from recall results entirely. |

## AgentLens memory log (#78)

Emit memory creates/supersessions (with a redaction flag) into AgentLens's
tamper-evident hash chain — one record spanning memory + the rest of the
platform. **Optional + non-blocking**: OFF unless both vars are set; emission is
fire-and-forget and never blocks or fails a memory write. Events land as
AgentLens `custom` events under a stable per-org session (`lore-memory:<org>`).

> Note: v1 is self-reported. Verified attribution (so events also appear in the
> AgentLens cross-product *timeline*, which keys on the server-verified agent id)
> is a tracked follow-up.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `LORE_AGENTLENS_URL` | none | No | AgentLens base URL. Set with `LORE_AGENTLENS_API_KEY` to enable emission. |
| `LORE_AGENTLENS_API_KEY` | none | No | AgentLens API key used to POST `/api/events`. |
| `LORE_AGENTLENS_TIMEOUT` | `3` | No | Per-emit HTTP timeout (seconds). |

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
