# API Reference

Base URL: `http://localhost:8765` (self-hosted) or your deployment URL.

All endpoints require `Authorization: Bearer <api_key>` except `/health`.

## Health Check

```
GET /health
```

```json
{"status": "ok"}
```

## Organization

### Initialize Org

```
POST /v1/org/init
```

```bash
curl -X POST http://localhost:8765/v1/org/init \
  -H "Content-Type: application/json" \
  -d '{"name": "my-org"}'
```

Response (201):
```json
{
  "org_id": "01HXYZ...",
  "api_key": "lore_sk_abc123...",
  "key_prefix": "lore_sk_abc1"
}
```

> ⚠️ Save the `api_key` — it's returned only once.

## API Keys

### Create Key

```
POST /v1/keys
```

Requires root key.

```json
{
  "name": "backend-agent",
  "project": "my-project",
  "is_root": false
}
```

Response (201):
```json
{
  "id": "01HXYZ...",
  "key": "lore_sk_...",
  "name": "backend-agent",
  "project": "my-project"
}
```

### List Keys

```
GET /v1/keys
```

Requires root key. Returns all keys for the org.

### Revoke Key

```
DELETE /v1/keys/{key_id}
```

Requires root key. Cannot revoke the last root key.

## Lessons

### Create Lesson

```
POST /v1/lessons
```

```json
{
  "problem": "Stripe API returns 429 after 100 req/min",
  "resolution": "Exponential backoff starting at 1s, cap at 32s",
  "tags": ["stripe", "rate-limit"],
  "confidence": 0.9,
  "embedding": [0.1, 0.2, ...],
  "source": "agent-1",
  "project": "payments"
}
```

`embedding` must be 384-dimensional. **The SDK computes this automatically** — you only need to provide embeddings when calling the API directly.

Response (201):
```json
{"id": "01HXYZ..."}
```

### Get Lesson

```
GET /v1/lessons/{lesson_id}
```

### List Lessons

```
GET /v1/lessons?project=payments&limit=20&offset=0
```

Response:
```json
{
  "lessons": [...],
  "total": 42,
  "limit": 20,
  "offset": 0
}
```

### Update Lesson

```
PATCH /v1/lessons/{lesson_id}
```

```json
{
  "confidence": 0.95,
  "tags": ["stripe", "rate-limit", "verified"],
  "upvotes": "+1"
}
```

`upvotes`/`downvotes` accept `"+1"` or `"-1"` for atomic increments.

### Delete Lesson

```
DELETE /v1/lessons/{lesson_id}
```

Returns 204 on success.

### Search Lessons

```
POST /v1/lessons/search
```

```json
{
  "embedding": [0.1, 0.2, ...],
  "tags": ["stripe"],
  "project": "payments",
  "limit": 5,
  "min_confidence": 0.0
}
```

Response:
```json
{
  "lessons": [
    {
      "id": "01HXYZ...",
      "problem": "Stripe API returns 429...",
      "resolution": "Exponential backoff...",
      "score": 0.847,
      ...
    }
  ]
}
```

Score = `cosine_similarity × confidence × time_decay × vote_factor`

### Export Lessons

```
POST /v1/lessons/export
```

Returns all lessons with embeddings (for backup/migration).

### Import Lessons

```
POST /v1/lessons/import
```

```json
{
  "lessons": [
    {
      "problem": "...",
      "resolution": "...",
      "embedding": [0.1, ...],
      "confidence": 0.8
    }
  ]
}
```

Upserts by ID. Response: `{"imported": 5}`

## Rate Limiting

100 requests per 60 seconds per API key. Returns 429 with `Retry-After` header when exceeded.

## Errors

All errors return:
```json
{
  "error": "error_code",
  "message": "Human-readable description"
}
```

| Code | Status | Description |
|------|--------|-------------|
| `missing_api_key` | 401 | No Authorization header |
| `invalid_api_key` | 401 | Key not found or malformed |
| `key_revoked` | 401 | Key has been revoked |
| `rate_limit_exceeded` | 429 | Too many requests |
| `validation_error` | 422 | Invalid request body |
| `not_found` | 404 | Resource not found |
