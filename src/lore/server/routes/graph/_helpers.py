"""Internal helpers for graph route sub-modules."""

from __future__ import annotations

from lore.server.routes._parsers import _parse_meta


async def _table_exists(conn, table_name: str) -> bool:
    return await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = $1)",
        table_name,
    )


def _memory_type(meta) -> str:
    m = _parse_meta(meta)
    return m.get("type", "general")


def _memory_tier(meta) -> str:
    m = _parse_meta(meta)
    return m.get("tier", "long")
