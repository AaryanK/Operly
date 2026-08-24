import os
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException

from apps.api import connectors_router, personal_connectors_router


SHARED_REDIRECT_URI = "https://operly.example/api/connectors/google/callback"


class _FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self.payload


class _FakeClientSession:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, data=None):
        return _FakeResponse(
            {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "expires_in": 3600,
                "scope": "openid email",
            }
        )

    def get(self, url, headers=None):
        return _FakeResponse({"email": "owner@example.test", "sub": "google-user"})


@asynccontextmanager
async def _fake_session_scope():
    yield object()


class GoogleConnectorOAuthRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "SESSION_SECRET": "test-session-secret",
                "GOOGLE_OAUTH_CLIENT_ID": "google-client",
                "GOOGLE_OAUTH_CLIENT_SECRET": "google-secret",
                "GOOGLE_OAUTH_REDIRECT_URI": SHARED_REDIRECT_URI,
            },
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()

    async def test_personal_connect_uses_registered_workspace_redirect_uri(self):
        auth = SimpleNamespace(user=SimpleNamespace(id="personal-user"))
        result = await personal_connectors_router.personal_google_connect(
            tier="assistant",
            auth=auth,
        )
        query = parse_qs(urlparse(result["authorization_url"]).query)
        self.assertEqual(query["redirect_uri"], [SHARED_REDIRECT_URI])
        ownership, data = connectors_router.load_google_oauth_state(query["state"][0])
        self.assertEqual(ownership, "personal")
        self.assertEqual(data["user_id"], "personal-user")
        self.assertEqual(data["ownership"], "personal")

    def test_workspace_state_is_still_accepted_as_workspace_owned(self):
        state = connectors_router.serializer().dumps(
            {"tenant_id": "tenant-1", "user_id": "owner-1", "tier": "basic"}
        )
        ownership, data = connectors_router.load_google_oauth_state(state)
        self.assertEqual(ownership, "workspace")
        self.assertEqual(data["tenant_id"], "tenant-1")
        self.assertEqual(connectors_router.redirect_uri(), SHARED_REDIRECT_URI)

    async def test_personal_state_shared_callback_stores_account_connector(self):
        state = personal_connectors_router.serializer().dumps(
            {"user_id": "personal-user", "tier": "assistant", "ownership": "personal"}
        )
        personal_upsert = AsyncMock()
        workspace_upsert = AsyncMock()
        with (
            patch.object(connectors_router.aiohttp, "ClientSession", _FakeClientSession),
            patch.object(connectors_router, "session_scope", _fake_session_scope),
            patch.object(connectors_router, "upsert_personal_google_connector", personal_upsert),
            patch.object(connectors_router, "upsert_google_connector", workspace_upsert),
        ):
            response = await connectors_router.google_callback(code="code", state=state)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/personal?connector=connected")
        personal_upsert.assert_awaited_once()
        self.assertEqual(personal_upsert.await_args.args[1], "personal-user")
        workspace_upsert.assert_not_awaited()

    async def test_workspace_shared_callback_keeps_tenant_connector_path(self):
        state = connectors_router.serializer().dumps(
            {"tenant_id": "tenant-1", "user_id": "owner-1", "tier": "basic"}
        )
        workspace_row = SimpleNamespace(id="connector-1", provider_account_id="owner@example.test")
        workspace_upsert = AsyncMock(return_value=workspace_row)
        personal_upsert = AsyncMock()
        append_event = AsyncMock()
        with (
            patch.object(connectors_router.aiohttp, "ClientSession", _FakeClientSession),
            patch.object(connectors_router, "session_scope", _fake_session_scope),
            patch.object(connectors_router, "upsert_google_connector", workspace_upsert),
            patch.object(connectors_router, "upsert_personal_google_connector", personal_upsert),
            patch.object(connectors_router, "append_event", append_event),
        ):
            response = await connectors_router.google_callback(code="code", state=state)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/dashboard?connector=connected&tier=basic")
        workspace_upsert.assert_awaited_once()
        self.assertEqual(workspace_upsert.await_args.args[1], "tenant-1")
        personal_upsert.assert_not_awaited()
        append_event.assert_awaited_once()

    def test_tampered_or_expired_state_is_rejected(self):
        personal_state = personal_connectors_router.serializer().dumps(
            {"user_id": "personal-user", "ownership": "personal"}
        )
        with self.assertRaises(HTTPException) as tampered:
            connectors_router.load_google_oauth_state(personal_state + "tampered")
        self.assertEqual(tampered.exception.status_code, 400)

        with self.assertRaises(HTTPException) as expired:
            connectors_router.load_google_oauth_state(personal_state, max_age=-1)
        self.assertEqual(expired.exception.status_code, 400)
