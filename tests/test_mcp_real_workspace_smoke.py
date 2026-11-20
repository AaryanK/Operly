"""Actual Operly MCP HTTP/OAuth/Kernel end-to-end, isolated synthetic Workspace.

No provider accounts, production database, model fixtures or substitute MCP
handlers. Uses FastAPI's true /mcp and /oauth routes, signed OAuth tokens,
live ClientGrant/membership checks, native Workspace capability execution.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import unittest
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import FastAPI

from apps.api.dependencies import get_auth_context
from apps.api.mcp_router import router
from apps.api.dependencies import get_db
from packages.database.db import SessionFactory, init_db
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.principal_models import ClientGrant
from packages.security.principals import PrincipalService

ORIGIN = "https://operly-staging-mcp.test"
CALLBACK = "https://dragonzpyder-staging.test/oauth/callback"


class RealWorkspaceMcpSmoke(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.environment = patch.dict(os.environ, {
            "OPERLY_ENV": "development",
            "PUBLIC_BASE_URL": ORIGIN,
            "OPERLY_MCP_TOKEN_SECRET": "isolated-test-signing-key-" + "z" * 48,  # pragma: allowlist secret (nonproduction test fixture)
            "OPERLY_MCP_CLIENTS_JSON": json.dumps({
                "dragonzpyder": {"name": "DragonZpyder staging",
                                 "redirect_uris": [CALLBACK]},
            }),
            "CHATGPT_MCP_REDIRECT_URIS": "",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        await init_db()
        self.user_id, self.workspace_id = str(uuid4()), str(uuid4())
        async with SessionFactory() as db:
            db.add(AppUser(id=self.user_id,
                email="mcp-smoke-" + self.user_id + "@example.invalid",
                display_name="Synthetic test principal", active=True))
            db.add(Tenant(id=self.workspace_id, name="Isolated MCP Test Workspace",
                          timezone="UTC"))
            db.add(TenantMember(user_id=self.user_id, tenant_id=self.workspace_id,
                                role="owner"))
            await db.flush()
            principal = await PrincipalService.user_principal(db, self.user_id)
            db.add(ClientGrant(principal_id=principal.id, tenant_id=self.workspace_id,
                client_id="dragonzpyder", scopes_json=json.dumps(["workspace:read"]),
                status="active"))
            await db.commit()
        self.app = FastAPI()
        self.app.include_router(router)
        async def fake_owner_auth():
            return SimpleNamespace(
                role="owner",
                user=SimpleNamespace(id=self.user_id),
                tenant=SimpleNamespace(id=self.workspace_id),
            )
        self.app.dependency_overrides[get_auth_context] = fake_owner_auth
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url=ORIGIN, follow_redirects=False,
        )
        self.addAsyncCleanup(self.http.aclose)
        self.access = await self._login()

    async def _login(self):
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(("verifier-" + "v" * 48).encode()).digest()
        ).rstrip(b"=").decode()
        authorize = await self.http.get("/oauth/authorize", params={
            "response_type": "code",
            "client_id": "dragonzpyder",
            "redirect_uri": CALLBACK,
            "resource": ORIGIN + "/mcp",
            "scope": "workspace:read",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "test-state",
        })
        self.assertEqual(authorize.status_code, 302, authorize.text[:300])
        location = authorize.headers["location"]
        self.assertEqual(urlsplit(location).netloc, "dragonzpyder-staging.test")
        code = parse_qs(urlsplit(location).query)["code"][0]
        token = await self.http.post("/oauth/token", data={
            "grant_type": "authorization_code", "client_id": "dragonzpyder",
            "redirect_uri": CALLBACK, "code": code,
            "code_verifier": "verifier-" + "v" * 48,
            "resource": ORIGIN + "/mcp",
        })
        self.assertEqual(token.status_code, 200, token.text[:300])
        return token.json()["access_token"]

    async def _rpc(self, method: str, params=None):
        message = {"jsonrpc": "2.0", "id": str(uuid4()), "method": method}
        if params is not None:
            message["params"] = params
        return await self.http.post("/mcp", json=message, headers={
            "Authorization": "Bearer " + self.access,
            "MCP-Protocol-Version": "2025-06-18",
            "Accept": "application/json, text/event-stream",
        })

    async def test_authenticated_oauth_tool_discovery_and_execution(self):
        response = await self.http.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/list",
        })
        self.assertEqual(response.status_code, 401)
        init = await self._rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "dragonzpyder-staging", "version": "0.1"},
        })
        self.assertEqual(init.status_code, 200, init.text[:300])
        self.assertIn("result", init.json())
        listed = await self._rpc("tools/list")
        self.assertEqual(listed.status_code, 200, listed.text[:300])
        tool_names = {tool["name"] for tool in listed.json()["result"]["tools"]}
        self.assertIn("workspace.describe", tool_names)
        self.assertIn("workspace.modules.list", tool_names)
        self.assertNotIn("tasks.create", tool_names)
        self.assertNotIn("tasks.list", tool_names)
        described = await self._rpc("tools/call",
            {"name": "workspace.describe", "arguments": {}})
        self.assertEqual(described.status_code, 200, described.text[:300])
        result = described.json()["result"]
        self.assertFalse(result.get("isError"), result)
        self.assertEqual(result["structuredContent"]["name"],
                         "Isolated MCP Test Workspace")
        modules = await self._rpc("tools/call",
            {"name": "workspace.modules.list", "arguments": {}})
        self.assertEqual(modules.status_code, 200, modules.text[:300])
        self.assertFalse(modules.json()["result"].get("isError"),
                         modules.text[:300])
        self.assertIsInstance(modules.json()["result"]["structuredContent"]["modules"], list)

    async def test_revocation_applies_to_next_tool_call(self):
        first = await self._rpc("tools/list")
        self.assertEqual(first.status_code, 200)
        async with SessionFactory() as db:
            from sqlalchemy import select
            row = await db.scalar(select(ClientGrant).where(
                ClientGrant.tenant_id == self.workspace_id,
                ClientGrant.client_id == "dragonzpyder"))
            row.status = "revoked"
            await db.commit()
        denied = await self._rpc("tools/call", {
            "name": "workspace.describe", "arguments": {},
        })
        self.assertEqual(denied.status_code, 401)
        self.assertIn("www-authenticate", denied.headers)


if __name__ == "__main__":
    unittest.main()
