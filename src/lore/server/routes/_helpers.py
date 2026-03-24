"""Shared SQL builder helpers used across route modules."""

from __future__ import annotations

from typing import Optional


def build_update(
    table: str,
    fields: dict,
    where_field: str,
    where_value,
) -> tuple[Optional[str], Optional[list]]:
    """Build a parameterized UPDATE SET statement from a dict of field->value.

    Skips fields whose value is None.  Returns (None, None) if no fields
    to update.

    Example::

        sql, params = build_update("slo_definitions", {"name": "new"}, "id", slo_id)
        # sql   = "UPDATE slo_definitions SET name = $1 WHERE id = $2"
        # params = ["new", slo_id]
    """
    set_parts: list[str] = []
    params: list = []
    for key, value in fields.items():
        if value is not None:
            params.append(value)
            set_parts.append(f"{key} = ${len(params)}")
    if not set_parts:
        return None, None
    params.append(where_value)
    sql = f"UPDATE {table} SET {', '.join(set_parts)} WHERE {where_field} = ${len(params)}"
    return sql, params
