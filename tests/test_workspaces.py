"""Tests for Workspaces + RBAC (F7)."""

from __future__ import annotations


class TestWorkspaceModels:
    def test_workspace_create_request(self):
        from lore.server.routes.workspaces import WorkspaceCreateRequest
        req = WorkspaceCreateRequest(name="Dev Team", slug="dev-team")
        assert req.name == "Dev Team"
        assert req.slug == "dev-team"
        assert req.settings == {}

    def test_workspace_response(self):
        from lore.server.routes.workspaces import WorkspaceResponse
        resp = WorkspaceResponse(
            id="ws-1", org_id="org-1",
            name="Dev Team", slug="dev-team",
        )
        assert resp.slug == "dev-team"

    def test_member_add_request(self):
        from lore.server.routes.workspaces import MemberAddRequest
        req = MemberAddRequest(user_id="user-1")
        assert req.role == "member"  # default

    def test_member_response(self):
        from lore.server.routes.workspaces import MemberResponse
        resp = MemberResponse(
            id="mem-1", workspace_id="ws-1",
            user_id="user-1", role="admin",
        )
        assert resp.role == "admin"


class TestAuditModels:
    def test_audit_entry(self):
        from lore.server.routes.audit import AuditEntry
        entry = AuditEntry(
            id=1, org_id="org-1",
            actor_id="key-1", actor_type="api_key",
            action="memory.create",
        )
        assert entry.action == "memory.create"
        assert entry.metadata == {}

    def test_audit_writer_import(self):
        from lore.server.audit import fire_audit_log, write_audit_log
        # Just verify imports work
        assert callable(write_audit_log)
        assert callable(fire_audit_log)
