"""Tests for Retention Policies (F6)."""

from __future__ import annotations


class TestPolicyModels:
    def test_policy_create_request_defaults(self):
        from lore.server.routes.policies import PolicyCreateRequest
        req = PolicyCreateRequest(name="prod")
        assert req.max_snapshots == 50
        assert req.is_active is True
        assert req.encryption_required is False
        assert req.retention_window == {"working": 3600, "short": 604800, "long": None}

    def test_policy_response(self):
        from lore.server.routes.policies import PolicyResponse
        resp = PolicyResponse(
            id="pol-1", org_id="org-1", name="prod",
            retention_window={"working": 3600, "short": 604800, "long": None},
            encryption_required=False,
            max_snapshots=30,
            is_active=True,
        )
        assert resp.name == "prod"
        assert resp.max_snapshots == 30

    def test_drill_result_response(self):
        from lore.server.routes.policies import DrillResultResponse
        drill = DrillResultResponse(
            id="drill-1",
            snapshot_name="snap-2024",
            status="success",
            recovery_time_ms=1500,
            memories_restored=100,
        )
        assert drill.status == "success"
        assert drill.recovery_time_ms == 1500

    def test_compliance_response(self):
        from lore.server.routes.policies import ComplianceResponse
        comp = ComplianceResponse(
            policy_id="pol-1",
            policy_name="prod",
            compliant=False,
            issues=["Snapshot count exceeds max"],
        )
        assert comp.compliant is False
        assert len(comp.issues) == 1


class TestRetentionTypes:
    def test_retention_policy_type(self):
        from lore.types import RetentionPolicy
        policy = RetentionPolicy(
            id="pol-1",
            org_id="org-1",
            name="prod",
        )
        assert policy.name == "prod"
        assert policy.max_snapshots == 50

    def test_restore_drill_result_type(self):
        from lore.types import RestoreDrillResult
        result = RestoreDrillResult(
            id="drill-1",
            org_id="org-1",
            snapshot_name="snap-1",
            status="success",
        )
        assert result.status == "success"
