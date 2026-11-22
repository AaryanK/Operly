"""Operly's identity tables are Workspace FK projections, not credential owners."""
import asyncio
from types import SimpleNamespace

from apps.api.identity_owner_gate import deny_local_identity_route, identity_owner_middleware


def test_local_identity_retired_when_dragonzpyder_is_authority(monkeypatch):
    monkeypatch.setenv("OPERLY_IDENTITY_OWNER", "dragonzpyder")
    for path in [
        "/api/auth/signup", "/api/auth/login", "/api/session/login",
        "/api/auth/google", "/api/auth/verify-email",
        "/api/auth/forgot-password", "/api/auth/reset-password",
        "/api/identities/discord/sign-in", "/api/identities/discord/callback",
    ]:
        assert deny_local_identity_route(path)
    for route in [
        "/api/auth/dragonzpyder/start",
        "/api/auth/dragonzpyder/callback",
        "/api/agent/chat", "/mcp", "/api/workspace",
        "/api/auth/workspaces",
    ]:
        assert not deny_local_identity_route(route)


def test_local_routes_allowed_only_before_cutover(monkeypatch):
    monkeypatch.delenv("OPERLY_IDENTITY_OWNER", raising=False)
    assert not deny_local_identity_route("/api/auth/signup")


def test_middleware_never_reaches_local_issuance_when_strict(monkeypatch):
    monkeypatch.setenv("OPERLY_IDENTITY_OWNER", "dragonzpyder")
    called = []
    async def next_handler(req):
        called.append(req.url.path)
        return "ok"
    request = SimpleNamespace(url=SimpleNamespace(path="/api/auth/google"))
    blocked = asyncio.run(identity_owner_middleware(request, next_handler))
    assert blocked.status_code == 410
    assert called == []
    request.url.path = "/api/auth/dragonzpyder/start"
    assert asyncio.run(identity_owner_middleware(request, next_handler)) == "ok"
    assert called == ["/api/auth/dragonzpyder/start"]
