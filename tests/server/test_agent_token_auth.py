"""AgentGate agent-token auth (#12 Phase 3).

An AgentGate-minted agent token (HS256 over the shared secret, typ:"agent")
is accepted as a principal, binding memories to the verified ``agt_*`` id.
"""

from __future__ import annotations

import base64
import json
import time

import pytest

pytest.importorskip("fastapi")
import jwt  # PyJWT

from lore.server.auth import AuthError, _resolve_agent_token, get_auth_context
from lore.server.config import settings

SECRET = "agentgate-shared-secret-at-least-32-chars!"


def _b64url(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


def _alg_none_token(sub: str) -> str:
    """An unsigned alg:none token — the classic algorithm-confusion attack."""
    return f"{_b64url({'alg': 'none', 'typ': 'JWT'})}.{_b64url({'sub': sub, 'typ': 'agent', 'tid': 'default'})}."


def _token(claims: dict, secret: str = SECRET, ttl: int = 900) -> str:
    now = int(time.time())
    return jwt.encode({"iat": now, "exp": now + ttl, **claims}, secret, algorithm="HS256")


def _agent_token(sub: str = "agt_abc", secret: str = SECRET, ttl: int = 900) -> str:
    return _token({"sub": sub, "typ": "agent", "tid": "default", "role": "viewer"}, secret, ttl)


@pytest.fixture
def agentgate_secret(monkeypatch):
    monkeypatch.setattr(settings, "agentgate_jwt_secret", SECRET)
    yield SECRET


class TestResolveAgentToken:
    def test_resolves_a_valid_agent_token_to_its_agent_id(self, agentgate_secret):
        ctx = _resolve_agent_token(_agent_token("agt_abc"))
        assert ctx is not None
        assert ctx.key_id == "agt_abc"
        assert ctx.principal_id == "agt_abc"  # raw agt_* — same string AgentLens stamps
        assert ctx.org_id == "default"
        assert ctx.role == "writer"
        assert ctx.is_root is False

    def test_rejects_a_user_token_without_typ_agent(self, agentgate_secret):
        # A signature-valid token that lacks typ:"agent" must not cross over.
        assert _resolve_agent_token(_token({"sub": "user_1", "role": "admin"})) is None

    def test_rejects_a_token_signed_with_a_different_secret(self, agentgate_secret):
        assert _resolve_agent_token(_agent_token("agt_abc", secret="a-different-secret-32-chars-long-xx")) is None

    def test_rejects_an_expired_token(self, agentgate_secret):
        assert _resolve_agent_token(_agent_token("agt_abc", ttl=-10)) is None

    def test_returns_none_when_no_shared_secret_is_configured(self, monkeypatch):
        monkeypatch.setattr(settings, "agentgate_jwt_secret", None)
        assert _resolve_agent_token(_agent_token("agt_abc")) is None

    def test_rejects_an_alg_none_token(self, agentgate_secret):
        # algorithms=["HS256"] must reject an unsigned alg:none token.
        assert _resolve_agent_token(_alg_none_token("agt_abc")) is None

    def test_rejects_a_token_with_missing_sub(self, agentgate_secret):
        assert _resolve_agent_token(_token({"typ": "agent", "tid": "default"})) is None

    def test_rejects_garbage(self, agentgate_secret):
        assert _resolve_agent_token("not-a-jwt") is None


class TestGetAuthContextDispatch:
    @pytest.mark.asyncio
    async def test_agent_token_accepted_even_in_api_key_only_mode(self, agentgate_secret, monkeypatch):
        # The agent-token trust anchor is independent of auth_mode.
        monkeypatch.setattr(settings, "auth_mode", "api-key-only")
        req = type("Req", (), {"headers": {"authorization": f"Bearer {_agent_token('agt_xyz')}"}})()
        ctx = await get_auth_context(req)
        assert ctx.principal_id == "agt_xyz"

    @pytest.mark.asyncio
    async def test_non_agent_token_still_rejected_in_api_key_only_mode(self, agentgate_secret, monkeypatch):
        monkeypatch.setattr(settings, "auth_mode", "api-key-only")
        req = type("Req", (), {"headers": {"authorization": f"Bearer {_token({'sub': 'user_1'})}"}})()
        with pytest.raises(AuthError):
            await get_auth_context(req)

    @pytest.mark.asyncio
    async def test_agent_token_accepted_in_oidc_required_mode(self, agentgate_secret, monkeypatch):
        # Intentional: the agent-token anchor is orthogonal to auth_mode, so it
        # works in oidc-required mode too (see get_auth_context docstring).
        monkeypatch.setattr(settings, "auth_mode", "oidc-required")
        req = type("Req", (), {"headers": {"authorization": f"Bearer {_agent_token('agt_oidc')}"}})()
        ctx = await get_auth_context(req)
        assert ctx.principal_id == "agt_oidc"
