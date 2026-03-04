"""Validate migration SQL file structure."""

from __future__ import annotations

from pathlib import Path


def test_migration_file_exists():
    migration = Path(__file__).parent.parent.parent / "migrations" / "001_initial.sql"
    assert migration.exists(), "Migration file 001_initial.sql not found"


def test_migration_creates_required_tables():
    migration = Path(__file__).parent.parent.parent / "migrations" / "001_initial.sql"
    sql = migration.read_text()

    assert "CREATE TABLE IF NOT EXISTS orgs" in sql
    assert "CREATE TABLE IF NOT EXISTS api_keys" in sql
    assert "CREATE TABLE IF NOT EXISTS memories" in sql


def test_migration_creates_required_indexes():
    migration = Path(__file__).parent.parent.parent / "migrations" / "001_initial.sql"
    sql = migration.read_text()

    assert "idx_keys_hash" in sql
    assert "idx_memories_org" in sql
    assert "idx_memories_org_project" in sql
    assert "idx_memories_embedding" in sql


def test_migration_enables_pgvector():
    migration = Path(__file__).parent.parent.parent / "migrations" / "001_initial.sql"
    sql = migration.read_text()

    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql


def test_migration_is_idempotent():
    """All CREATE statements should use IF NOT EXISTS."""
    migration = Path(__file__).parent.parent.parent / "migrations" / "001_initial.sql"
    sql = migration.read_text()

    # Every CREATE TABLE should be IF NOT EXISTS
    import re

    tables = re.findall(r"CREATE TABLE\b", sql)
    tables_ine = re.findall(r"CREATE TABLE IF NOT EXISTS", sql)
    assert len(tables) == len(tables_ine), "All CREATE TABLE must use IF NOT EXISTS"
