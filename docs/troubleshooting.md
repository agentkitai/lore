# Troubleshooting

Common issues and solutions when running Lore.

---

## Setup Issues

### Docker containers fail to start

**Symptom:** `docker compose up` exits with errors or the `lore` service restarts repeatedly.

**Solutions:**
- Ensure Docker and Docker Compose v2 are installed: `docker compose version`
- Check that the `pgdata` volume exists: `docker volume create agentkit-stack_lore-db-data`
- Verify port 5432 and 8765 are not in use: `lsof -i :5432` / `lsof -i :8765`
- Check container logs: `docker compose logs lore` and `docker compose logs db`

### PostgreSQL connection refused

**Symptom:** `connection refused` or `could not connect to server` errors.

**Solutions:**
- Wait for the health check — Postgres may still be initializing
- Verify `DATABASE_URL` is set correctly: `postgresql://lore:lore@db:5432/lore` (Docker) or `postgresql://lore:lore@localhost:5432/lore` (host)
- Check that the `db` service is healthy: `docker compose ps`
- If running outside Docker, ensure `pg_hba.conf` allows connections from your host

### pgvector extension missing

**Symptom:** `/ready` returns `{"pgvector": false}` or queries fail with `type "vector" does not exist`.

**Solutions:**
- Use the `pgvector/pgvector:pg16` Docker image, which includes the extension
- If using a custom Postgres, install pgvector: `CREATE EXTENSION IF NOT EXISTS vector;`
- Verify: `SELECT * FROM pg_extension WHERE extname = 'vector';`

### Migrations fail

**Symptom:** Server crashes at startup with migration errors.

**Solutions:**
- Check that `MIGRATIONS_DIR` points to the correct directory (default: `migrations`)
- Ensure the database user has CREATE TABLE permissions
- Review migration SQL files for syntax errors
- Connect directly and check schema state: `\dt` in psql

---

## Authentication Problems

### 401 Unauthorized on every request

**Symptom:** All API calls return `{"error": "unauthorized"}`.

**Solutions:**
- Ensure the `Authorization: Bearer <api_key>` header is set
- Verify the API key was created via `POST /v1/org/init` or `POST /v1/keys`
- API keys are hashed — the raw key is only shown once at creation time
- Check `AUTH_MODE` is set to `api-key-only` (default) unless using OIDC

### OIDC / JWT validation fails

**Symptom:** `invalid_token` errors when using JWT authentication.

**Solutions:**
- Verify `OIDC_ISSUER` matches the issuer claim in your JWT
- Check `OIDC_AUDIENCE` matches the audience claim
- Ensure the OIDC provider's JWKS endpoint is reachable from the server
- Set `AUTH_MODE=dual` to allow both API keys and JWTs

### Remote store authentication

**Symptom:** `lore recall` returns connection errors or 401.

**Solutions:**
- Set both `LORE_API_URL` and `LORE_API_KEY` in your environment
- Set `LORE_STORE=remote` to use the HTTP backend
- Verify the server is reachable: `curl $LORE_API_URL/health`

---

## Retrieval Quality

### No results from recall / low relevance scores

**Symptom:** `recall` returns empty results or low-scoring matches.

**Solutions:**
- Ensure memories have embeddings — check that the embedding was generated at creation time
- Run `lore reindex` to re-embed all memories if the embedding model was changed
- Lower the minimum score threshold (default `min_score=0.3` on `/v1/retrieve`)
- Use more specific or descriptive queries — semantic search works best with natural language
- Check that the target memories are not expired (tier TTLs: working=1h, short=7d, long=no expiry)

### Duplicate or redundant results

**Symptom:** Multiple near-identical memories in results.

**Solutions:**
- Run `lore consolidate --execute --strategy deduplicate` to merge duplicates
- Use `lore consolidate --dry-run` first to preview what would be merged
- Enable enrichment (`LORE_ENRICHMENT_ENABLED=true`) for better metadata-based filtering

### Embedding model mismatch

**Symptom:** Scores are uniformly low across all queries.

**Solutions:**
- The default model is MiniLM-L6-v2 (384 dimensions)
- If you changed the embedder, run `lore reindex --dual` to regenerate all embeddings
- Ensure `embedding` column values are 384-dimensional vectors

---

## Performance

### Slow queries

**Symptom:** Recall or retrieve takes more than 1-2 seconds.

**Solutions:**
- Ensure pgvector has an IVFFlat or HNSW index on the `embedding` column
- Reduce `limit` on search queries — fewer results means faster vector scans
- Check database connection pool — large numbers of concurrent requests may exhaust connections
- Monitor with `EXPLAIN ANALYZE` on the raw SQL in psql
- Consider adding `project` filters to narrow the search scope

### High memory usage

**Symptom:** Server or MCP process consumes excessive RAM.

**Solutions:**
- The ONNX embedding model loads into memory (~90MB) — this is expected
- If running many workers, each loads its own model copy; reduce worker count or use a shared embedding service
- Set `LORE_SNAPSHOT_THRESHOLD` lower (default 30000 chars) to trigger snapshots earlier and free accumulator memory
- Monitor with `/metrics` endpoint if `METRICS_ENABLED=true`

### Slow startup

**Symptom:** Server takes a long time to become ready.

**Solutions:**
- Migrations run at startup — large databases may take time on first run
- The embedding model is loaded lazily on first request, not at startup
- Check `/ready` to distinguish between database and pgvector issues

---

## Integration Issues

### Claude Code hooks not triggering

**Symptom:** Lore does not automatically save or recall during Claude Code sessions.

**Solutions:**
- Verify `.claude/hooks.json` is configured correctly (see `docs/setup-claude-code.md`)
- Hooks require the `lore` CLI to be on `$PATH`
- Test manually: `lore recall "test query"` from the same shell
- Check that `LORE_STORE` and `LORE_API_URL` / `LORE_API_KEY` are set in the hook environment

### MCP server not connecting

**Symptom:** Claude Desktop or other MCP clients cannot connect to Lore.

**Solutions:**
- Start the MCP server manually to test: `lore mcp`
- Check that the MCP configuration points to the correct command and arguments
- Review stderr output for import errors (missing dependencies)
- Install server dependencies: `pip install lore-sdk[server]`
- Verify the correct Python environment is activated

### MCP tools return errors

**Symptom:** Tools like `remember` or `recall` fail with exceptions.

**Solutions:**
- Check that the database is accessible (local SQLite or remote server)
- For knowledge graph tools, ensure `LORE_KNOWLEDGE_GRAPH=true`
- For enrichment tools, ensure `LORE_ENRICHMENT_ENABLED=true` and LLM credentials are set
- Review MCP server logs for detailed error messages

### GitHub sync fails

**Symptom:** `github_sync` tool returns errors.

**Solutions:**
- Install and authenticate the GitHub CLI: `gh auth status`
- Ensure the `gh` command is on `$PATH`
- Verify the repo format is `owner/repo` (e.g., `octocat/Hello-World`)
- Check GitHub API rate limits: `gh api rate_limit`
