-- Migration 021: Add ``fts_weight`` to retrieval_profiles (Phase 6C hybrid retrieval)
-- This is the per-profile multiplier for the FTS branch in RRF fusion.
-- Default 1.0 — same weight as vector similarity by default; presets can override.

ALTER TABLE retrieval_profiles
    ADD COLUMN IF NOT EXISTS fts_weight REAL NOT NULL DEFAULT 1.0;
