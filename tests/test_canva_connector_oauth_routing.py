import os
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException

from apps.api import connectors_router, personal_connectors_router
from packages.connectors import canva_provider


CANVA_REDIRECT_URI = "https://operly.example/api/connectors/canva/callback"


@asynccontextmanager
async def _fake_session_scope():
    yield object()


class CanvaConnectorOAuthRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "SESSION_SECRET": "test-session-secret",
                "CANVA_CLIENT_ID": "canva-client",
                "CANVA_CLIENT_SECRET": "canva-secret",
                "CANVA_REDIRECT_URI": CANVA_REDIRECT_URI,
            },
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()

    async def test_personal_connect_uses_shared_callback_and_pkce(self):
        auth = SimpleNamespace(user=SimpleNamespace(id="personal-user"))
        db = SimpleNamespace(commit=AsyncMock())
        with (
            patch.object(personal_connectors_router, "store_account_secret", AsyncMock(return_value="pkce-1")),
            patch.object(personal_connectors_router, "canva_pkce_pair", return_value=("verifier", "challenge")),
        ):
            result = await personal_connectors_router.personal_canva_connect(auth=auth, db=db)

        query = parse_qs(urlparse(result["authorization_url"]).query)
        self.assertEqual(query["redirect_uri"], [CANVA_REDIRECT_URI])
        self.assertEqual(query["code_challenge"], ["challenge"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["scope"], [" ".join(canva_provider.CANVA_SCOPES)])
        ownership, data = connectors_router.load_canva_oauth_state(query["state"][0])
        self.assertEqual(ownership, "personal")
        self.assertEqual(data["user_id"], "personal-user")
        self.assertEqual(data["pkce_ref"], "pkce-1")
        db.commit.assert_awaited_once()

    async def test_workspace_connect_binds_state_to_tenant_and_owner(self):
        auth = SimpleNamespace(
            role="owner",
            user=SimpleNamespace(id="owner-1"),
            tenant=SimpleNamespace(id="tenant-1"),
        )
        db = SimpleNamespace(commit=AsyncMock())
        with (
            patch.object(connectors_router, "store_secret", AsyncMock(return_value="pkce-2")),
            patch.object(connectors_router, "canva_pkce_pair", return_value=("verifier", "challenge")),
        ):
            result = await connectors_router.canva_connect(auth=auth, db=db)

        query = parse_qs(urlparse(result["authorization_url"]).query)
        ownership, data = connectors_router.load_canva_oauth_state(query["state"][0])
        self.assertEqual(ownership, "workspace")
        self.assertEqual(data["tenant_id"], "tenant-1")
        self.assertEqual(data["user_id"], "owner-1")
        self.assertEqual(data["pkce_ref"], "pkce-2")
        db.commit.assert_awaited_once()

    async def test_personal_callback_stores_only_account_connector(self):
        state = personal_connectors_router.canva_serializer().dumps(
            {"ownership": "personal", "user_id": "personal-user", "pkce_ref": "pkce-1"}
        )
        personal_upsert = AsyncMock()
        workspace_upsert = AsyncMock()
        with (
            patch.object(connectors_router, "consume_canva_pkce", AsyncMock(return_value="verifier")),
            patch.object(connectors_router, "canva_exchange_code", AsyncMock(return_value={"access_token": "token", "refresh_token": "refresh", "expires_in": 14400, "scope": "profile:read design:meta:read"})),
            patch.object(connectors_router, "canva_get_identity", AsyncMock(return_value={"user_id": "canva-user", "team_id": "team-1", "display_name": "Canva User"})),
            patch.object(connectors_router, "session_scope", _fake_session_scope),
            patch.object(connectors_router, "upsert_personal_canva_connector", personal_upsert),
            patch.object(connectors_router, "upsert_canva_connector", workspace_upsert),
        ):
            response = await connectors_router.canva_callback(code="code", state=state)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/personal?connector=connected&provider=canva")
        personal_upsert.assert_awaited_once()
        self.assertEqual(personal_upsert.await_args.args[1], "personal-user")
        workspace_upsert.assert_not_awaited()

    async def test_workspace_callback_stores_tenant_connector_and_event(self):
        state = connectors_router.canva_serializer().dumps(
            {"ownership": "workspace", "tenant_id": "tenant-1", "user_id": "owner-1", "pkce_ref": "pkce-2"}
        )
        workspace_row = SimpleNamespace(id="connector-1", provider_account_id="canva-user")
        workspace_upsert = AsyncMock(return_value=workspace_row)
        personal_upsert = AsyncMock()
        append_event = AsyncMock()
        with (
            patch.object(connectors_router, "consume_canva_pkce", AsyncMock(return_value="verifier")),
            patch.object(connectors_router, "canva_exchange_code", AsyncMock(return_value={"access_token": "token", "refresh_token": "refresh", "expires_in": 14400, "scope": "profile:read design:meta:read"})),
            patch.object(connectors_router, "canva_get_identity", AsyncMock(return_value={"user_id": "canva-user", "team_id": "team-1", "display_name": "Canva User"})),
            patch.object(connectors_router, "session_scope", _fake_session_scope),
            patch.object(connectors_router, "upsert_canva_connector", workspace_upsert),
            patch.object(connectors_router, "upsert_personal_canva_connector", personal_upsert),
            patch.object(connectors_router, "append_event", append_event),
        ):
            response = await connectors_router.canva_callback(code="code", state=state)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/dashboard?connector=connected&provider=canva")
        workspace_upsert.assert_awaited_once()
        self.assertEqual(workspace_upsert.await_args.args[1], "tenant-1")
        personal_upsert.assert_not_awaited()
        append_event.assert_awaited_once()

    def test_tampered_or_expired_canva_state_is_rejected(self):
        state = personal_connectors_router.canva_serializer().dumps(
            {"ownership": "personal", "user_id": "personal-user", "pkce_ref": "pkce-1"}
        )
        with self.assertRaises(HTTPException) as tampered:
            connectors_router.load_canva_oauth_state(state + "tampered")
        self.assertEqual(tampered.exception.status_code, 400)

        with self.assertRaises(HTTPException) as expired:
            connectors_router.load_canva_oauth_state(state, max_age=-1)
        self.assertEqual(expired.exception.status_code, 400)

    def test_canva_scopes_map_to_governed_capability_names(self):
        capabilities = set(canva_provider.canva_capabilities(set(canva_provider.CANVA_SCOPES)))
        self.assertIn("canva.get_profile", capabilities)
        self.assertIn("canva.create_design", capabilities)
        self.assertIn("canva.upload_asset", capabilities)
        self.assertIn("canva.export_design", capabilities)
