"""Pre-registered external MCP clients cannot bypass user/workspace authority."""
import json
import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from apps.api.mcp_router import _allowed_redirect_uris, _verify_client
from packages.mcp.clients import external_client_catalog, get_registered_client, registered_clients

CHATGPT = "https://chatgpt.com/connector_platform_oauth_redirect"
CLAUDE = "https://claude.ai/api/mcp/auth_callback"
CODEX = "http://127.0.0.1:57341/callback"
DRAGONZPYDER = "https://dragonzpyder.xyz/oauth/operly/callback"

DEFINITIONS = json.dumps({
    "claude": {"name": "Claude", "redirect_uris": [CLAUDE]},
    "codex": {"name": "Codex", "redirect_uris": [CODEX]},
    "dragonzpyder": {"name": "DragonZpyder", "redirect_uris": [DRAGONZPYDER],
                     "secret_env": "OPERLY_MCP_DRAGONZPYDER_SECRET"},
})


class MultiClientMcpTests(unittest.TestCase):
    def config(self, **updates):
        env = {
            "CHATGPT_MCP_REDIRECT_URIS": CHATGPT,
            "OPERLY_MCP_CLIENTS_JSON": DEFINITIONS,
            "CHATGPT_MCP_CLIENT_SECRET": "",
        }
        env.update(updates)
        return patch.dict(os.environ, env)

    def test_configured_external_clients_have_separate_exact_redirects(self):
        with self.config():
            clients = registered_clients()
            self.assertEqual(set(clients), {"chatgpt", "claude", "codex", "dragonzpyder"})
            self.assertEqual(_allowed_redirect_uris("claude"), {CLAUDE})
            self.assertEqual(_allowed_redirect_uris("codex"), {CODEX})
            self.assertEqual(_allowed_redirect_uris("dragonzpyder"), {DRAGONZPYDER})
            self.assertEqual(_allowed_redirect_uris("chatgpt"), {CHATGPT})
            self.assertEqual(_allowed_redirect_uris("unknown"), set())
            self.assertEqual({x["id"] for x in external_client_catalog()}, set(clients))

    def test_authentication_does_not_accept_unregistered_or_unprovisioned_client(self):
        with self.config():
            self.assertIsNone(get_registered_client("random-agent"))
            with self.assertRaises(HTTPException):
                _verify_client("random-agent", None)
            # Public clients rely on S256 PKCE + exact redirect + owner grant.
            _verify_client("claude", None)
            _verify_client("codex", None)
            _verify_client("chatgpt", None)
            # Confidential DragonZpyder remains disabled until provisioned.
            with self.assertRaises(HTTPException):
                _verify_client("dragonzpyder", None)
        with self.config(OPERLY_MCP_DRAGONZPYDER_SECRET="s" * 48):
            with self.assertRaises(HTTPException):
                _verify_client("dragonzpyder", None)
            with self.assertRaises(HTTPException):
                _verify_client("dragonzpyder", "wrong")
            _verify_client("dragonzpyder", "s" * 48)

    def test_legacy_chatgpt_secret_remains_enforced(self):
        with self.config(CHATGPT_MCP_CLIENT_SECRET="x" * 48):
            with self.assertRaises(HTTPException):
                _verify_client("chatgpt", None)
            _verify_client("chatgpt", "x" * 48)

    def test_fail_closed_invalid_registration_and_redirect(self):
        samples = (
            {"evil": {"redirect_uris": ["http://evil.example/cb"]}},
            {"evil": {"redirect_uris": ["https://a.example/cb#fragment"]}},
            {"evil": {"redirect_uris": ["https://a.example/cb?next=1"]}},
            {"evil": {"redirect_uris": ["https://user:pass@a.example/cb"]}},
            {"chatgpt": {"redirect_uris": [CLAUDE]}},
            {"codex": {"redirect_uris": [CODEX, CODEX]}},
            {"codex": {"redirect_uris": [CODEX], "secret_env": "SESSION_SECRET"}},
        )
        for item in samples:
            with self.subTest(item=item), self.config(
                OPERLY_MCP_CLIENTS_JSON=json.dumps(item),
            ):
                with self.assertRaises(ValueError):
                    registered_clients()

    def test_remote_mcp_metadata_and_unauthenticated_tool_access(self):
        """Actual ASGI endpoints advertise discovery but never leak tools anonymously."""
        import asyncio
        import httpx
        from fastapi import FastAPI
        from apps.api.dependencies import get_db
        from apps.api.mcp_router import router

        async def fake_db():
            yield None

        async def run():
            app = FastAPI()
            app.include_router(router)
            app.dependency_overrides[get_db] = fake_db
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://mcp.operly.example",
            ) as client:
                meta = await client.get("/.well-known/oauth-authorization-server")
                self.assertEqual(meta.status_code, 200)
                self.assertIn("none", meta.json()["token_endpoint_auth_methods_supported"])
                self.assertIn("client_secret_post", meta.json()["token_endpoint_auth_methods_supported"])
                protected = await client.get("/.well-known/oauth-protected-resource/mcp")
                self.assertEqual(protected.status_code, 200)
                self.assertEqual(protected.json()["resource"], "https://mcp.operly.example/mcp")
                unauth = await client.post("/mcp", json={
                    "jsonrpc": "2.0", "id": 7, "method": "tools/list",
                })
                self.assertEqual(unauth.status_code, 401)
                self.assertIn("resource_metadata", unauth.headers["www-authenticate"])

        with self.config(
            PUBLIC_BASE_URL="https://mcp.operly.example",
            OPERLY_MCP_DRAGONZPYDER_SECRET="s" * 48,
        ):
            asyncio.run(run())


    def test_no_accidental_new_client_without_explicit_configuration(self):
        with patch.dict(os.environ, {
            "CHATGPT_MCP_REDIRECT_URIS": CHATGPT,
            "OPERLY_MCP_CLIENTS_JSON": "",
        }):
            self.assertEqual(set(registered_clients()), {"chatgpt"})


if __name__ == "__main__":
    unittest.main()
