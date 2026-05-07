# Lore Progressive Disclosure (Phase 6D) — Design

**Status:** Approved (autonomous trust mandate), pending implementation.
**Date:** 2026-05-07

## Goal

Cut the per-prompt context cost of retrieval ~10× by separating **search** (cheap index returning IDs + titles + scores) from **detail fetch** (full memory content for a chosen subset). claude-mem demonstrated this pattern: ~50 tokens/result for search, full payload only when the agent asks for specific IDs.

After 6D: the auto-retrieval hook returns a compact index by default; the MCP `recall` tool gets a `detail` mode for callers that want the full payload up-front; agents that want to drill in can call `get_memories(ids=[...])` after surveying the index.

## Non-goals

- Rewriting the recall ranking. 6C already did that.
- Storing memories in two tiers. The index is computed on the fly from the same data 6C produces.
- Compression / summarization of full memory payloads. If a memory is long, it's long. Future work.
- A separate caching layer. Lore's existing access counters already track which memories are recent.

## Design decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | New endpoint or extend `/v1/retrieve`? | **New.** `GET /v1/search` returns the compact index. `/v1/retrieve` keeps working unchanged for callers that want full payloads in one round-trip. |
| 2 | Compact index shape? | `{id, title, score, signals}` per memory. Title comes from `meta.title` (Phase 6B observations) or first 80 chars of `content` (everything else). |
| 3 | Detail-fetch endpoint? | `GET /v1/memories/details?ids=mem_1,mem_2,...` (existing memories list endpoint extended to accept an `ids` filter). |
| 4 | New MCP tools? | Yes — `mcp__lore__search(query)` and `mcp__lore__get_memories(ids=...)`. The existing `recall` tool stays for one-shot full-payload retrieval. |
| 5 | Should the auto-retrieval hook switch to progressive disclosure? | Yes — but as an optional `LORE_PROGRESSIVE=true` env var (default OFF for now). The hook is already injecting low-volume content; switching default behavior is a separate decision. Make the option available; let users opt in. |
| 6 | Token-cost target? | ~50 tokens per indexed result vs ~300 tokens per full memory. 5–10× savings depending on content length. |
| 7 | Default `limit` for search? | 20 results in the index. Detail fetch caps at 10 IDs per call. |

## Architecture

```
            ┌─────────────────────────────────┐
agent  ───▶ │ GET /v1/search?query=...        │ ───▶ [{id, title, score, signals}, ...]
            └─────────────────────────────────┘     ~50 tokens/result

            (agent picks top-k of interest)

            ┌─────────────────────────────────┐
agent  ───▶ │ GET /v1/memories/details?ids=...│ ───▶ [full StoredMemory, ...]
            └─────────────────────────────────┘     ~300 tokens/result
```

### Invariants

- **`/v1/search` is `/v1/retrieve` minus the heavy fields.** Same hybrid scoring, same profile resolution. Only the response shape differs: drop `content`, `tags`, `meta`, `created_at` (keep `id, title, score, signals` only).
- **`/v1/memories/details?ids=...`** is auth-scoped and returns 404 for any unknown id (don't leak existence).
- **Title generation is deterministic.** Same input → same title. Computed from the row, no LLM involved.
- **Hook backward compat.** `LORE_PROGRESSIVE` defaults `false`; existing hook behavior unchanged unless opted in.

## Components

### New endpoints

| Path | Method | Returns |
|------|--------|---------|
| `/v1/search` | GET | Compact index `[{id, title, score, signals}]` |
| `/v1/memories/details` | GET | Full `StoredMemory` payloads for the given `ids=` CSV |

Both endpoints reuse the existing auth + profile-resolution machinery.

### New MCP tools

```python
@mcp.tool()
def search(query: str, limit: int = 20, min_score: float = 0.3) -> str:
    """Return a compact index of relevant memories: just id + title + score.
    Use get_memories(ids=...) to fetch full content for the ones you want."""

@mcp.tool()
def get_memories(ids: List[str]) -> str:
    """Fetch full content for one or more memory IDs returned by search()."""
```

The existing `recall(query)` tool stays as-is (single round-trip with full payloads).

### Title generation

```python
def memory_title(m: StoredMemory) -> str:
    # Prefer Phase 6B observation title.
    title = (m.meta or {}).get("title")
    if title:
        return title[:80]
    # Fallback: first sentence or 80 chars of content.
    text = m.content.strip().split("\n", 1)[0]
    return text[:80] + ("…" if len(text) > 80 else "")
```

### Hook integration (opt-in)

`LORE_CAPTURE_TOOL_HOOK_SCRIPT` is unaffected (capture flow doesn't disclose memories).

The retrieval hook (`CLAUDE_CODE_HOOK_SCRIPT` in setup.py) gets a new branch:

```python
if os.environ.get("LORE_PROGRESSIVE", "false").lower() == "true":
    # Two-phase: search first, then fetch top-K details.
    index = _search(query, limit=20)
    survivors = [r for r in index if r["score"] >= min_score][:5]
    if not survivors:
        return
    full = _get_memories(ids=[r["id"] for r in survivors])
    formatted = _format(full)
else:
    # Existing one-shot path.
    formatted = _retrieve_classic(query)
```

## Tests

| Layer | Coverage |
|-------|----------|
| HTTP | `/v1/search` returns compact shape; verifies `signals` carries through; auth required. |
| HTTP | `/v1/memories/details?ids=...` returns full payloads for valid ids; 404 for missing. |
| MCP | `search()` and `get_memories()` round-trip via the Lore client. |
| Unit | `memory_title()` deterministic for various inputs (observation w/ title, plain content, empty content edge case). |
| Hook | Render hook with `LORE_PROGRESSIVE=true`; assert it calls `/v1/search` then `/v1/memories/details`. |
| Token budget | Compact index payload size measured against full payload — assert at least 5× reduction on a fixture. |

## Scope

| Component | LOC |
|---|---|
| `server/routes/search.py` | ~80 |
| `server/routes/memories.py` (extend list to accept `ids`) | ~30 |
| `services/retrieve.py` (extract `search()` slim path) | ~80 |
| MCP tools | ~50 |
| Hook progressive branch | ~70 |
| Title helper | ~25 |
| Tests | ~250 |
| Docs | ~30 |
| **Total** | **~615** |

## Out of scope

- Streaming / chunked detail fetch (e.g. for 1000-result audits). Yagni for v1.
- Server-side caching of search index. The hybrid scoring is fast enough.
- Re-ranking after detail fetch (e.g. by reading content with a larger model). Future work.
