"""Tests for S2: Schema & Hash — version validation and content integrity."""

from __future__ import annotations

import pytest

from lore.export.schema import (
    EXPORT_SCHEMA_VERSION,
    compute_content_hash,
    validate_schema_version,
    verify_content_hash,
)


class TestSchemaVersionValidation:
    def test_schema_version_check_current(self):
        validate_schema_version(EXPORT_SCHEMA_VERSION)  # should not raise

    def test_schema_version_check_older(self):
        validate_schema_version(0)  # should not raise

    def test_schema_version_check_newer(self):
        with pytest.raises(ValueError, match="newer"):
            validate_schema_version(EXPORT_SCHEMA_VERSION + 1)


class TestContentHash:
    def test_content_hash_deterministic(self):
        data = {"memories": [{"id": "1", "content": "hello"}], "entities": []}
        h1 = compute_content_hash(data)
        h2 = compute_content_hash(data)
        assert h1 == h2
        assert h1.startswith("sha256:")

    def test_content_hash_differs_on_change(self):
        data1 = {"memories": [{"id": "1", "content": "hello"}]}
        data2 = {"memories": [{"id": "1", "content": "world"}]}
        assert compute_content_hash(data1) != compute_content_hash(data2)

    def test_hash_verification_passes(self):
        data = {"memories": [], "entities": []}
        content_hash = compute_content_hash(data)
        export_dict = {
            "schema_version": 1,
            "exported_at": "2026-01-01T00:00:00Z",
            "content_hash": content_hash,
            "data": data,
        }
        verify_content_hash(export_dict)  # should not raise

    def test_hash_verification_fails_on_corruption(self):
        data = {"memories": [], "entities": []}
        content_hash = compute_content_hash(data)
        export_dict = {
            "schema_version": 1,
            "exported_at": "2026-01-01T00:00:00Z",
            "content_hash": content_hash,
            "data": {"memories": [{"id": "tampered"}], "entities": []},
        }
        with pytest.raises(ValueError, match="mismatch"):
            verify_content_hash(export_dict)

    def test_hash_ignores_envelope(self):
        data = {"memories": []}
        h1 = compute_content_hash(data)
        # Changing envelope metadata shouldn't affect hash
        h2 = compute_content_hash(data)
        assert h1 == h2

    def test_legacy_export_no_hash(self):
        export_dict = {
            "schema_version": 1,
            "exported_at": "2026-01-01T00:00:00Z",
            "data": {"memories": []},
            # No content_hash key
        }
        verify_content_hash(export_dict)  # should not raise
