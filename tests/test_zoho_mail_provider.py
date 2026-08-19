import json
import os
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx

from packages.email.providers.base import EmailEnvelope
from packages.email.providers.zoho_mail_api import (
    ZohoMailAPIProvider,
    ZohoMailAPIError,
    ZohoMailConfigurationError,
)
from packages.email.service import get_email_service, set_email_service_for_tests


CONFIG = {
    "MAIL_PROVIDER": "zoho_mail_api",
    "OPERLY_FROM_EMAIL": "operly@example.com",
    "ZOHO_MAIL_ACCOUNT_ID": "123456789",
    "ZOHO_MAIL_CLIENT_ID": "client-id",
    "ZOHO_MAIL_CLIENT_SECRET": "client-secret",
    "ZOHO_MAIL_REFRESH_TOKEN": "refresh-token",
    "ZOHO_ACCOUNTS_BASE_URL": "https://accounts.zoho.com",
    "ZOHO_MAIL_API_BASE_URL": "https://mail.zoho.com/api",
    "ZOHO_MAIL_TIMEOUT_SECONDS": "5",
}


def envelope() -> EmailEnvelope:
    return EmailEnvelope(
        to_email="owner@example.com",
        subject="Verify your OPERLY email",
        html_body="<p>Verification content</p>",
        text_body="Verification content",
    )


class ZohoMailAPIProviderTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        set_email_service_for_tests(None)

    async def test_refreshes_once_caches_token_and_sends_documented_payload(self):
        token_requests = 0
        messages = []

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal token_requests
            if request.url.path == "/oauth/v2/token":
                token_requests += 1
                form = parse_qs(request.content.decode())
                self.assertEqual(form["grant_type"], ["refresh_token"])
                self.assertEqual(form["client_id"], ["client-id"])
                self.assertEqual(form["client_secret"], ["client-secret"])
                self.assertEqual(form["refresh_token"], ["refresh-token"])
                self.assertNotIn("client_secret", request.url.query.decode())
                return httpx.Response(200, json={"access_token": "access-token", "expires_in": 3600})
            self.assertEqual(request.url.path, "/api/accounts/123456789/messages")
            self.assertEqual(request.headers["authorization"], "Zoho-oauthtoken access-token")
            messages.append(json.loads(request.content))
            return httpx.Response(200, json={"status": {"code": 200, "description": "success"}})

        with patch.dict(os.environ, CONFIG, clear=False):
            provider = ZohoMailAPIProvider(transport=httpx.MockTransport(handler))
            await provider.send(envelope())
            await provider.send(envelope())

        self.assertEqual(token_requests, 1)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["fromAddress"], "operly@example.com")
        self.assertEqual(messages[0]["toAddress"], "owner@example.com")
        self.assertEqual(messages[0]["mailFormat"], "html")
        self.assertEqual(messages[0]["encoding"], "UTF-8")

    async def test_unauthorized_send_refreshes_and_retries_once(self):
        token_requests = 0
        message_requests = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal token_requests, message_requests
            if request.url.path == "/oauth/v2/token":
                token_requests += 1
                return httpx.Response(
                    200,
                    json={"access_token": f"access-token-{token_requests}", "expires_in": 3600},
                )
            message_requests += 1
            if message_requests == 1:
                return httpx.Response(401, json={"error": "INVALID_OAUTHTOKEN"})
            self.assertEqual(
                request.headers["authorization"],
                "Zoho-oauthtoken access-token-2",
            )
            return httpx.Response(200, json={"status": {"code": 200}})

        with patch.dict(os.environ, CONFIG, clear=False):
            provider = ZohoMailAPIProvider(transport=httpx.MockTransport(handler))
            await provider.send(envelope())

        self.assertEqual(token_requests, 2)
        self.assertEqual(message_requests, 2)

    def test_rejects_missing_credentials_and_non_zoho_endpoints(self):
        missing = {**CONFIG, "ZOHO_MAIL_REFRESH_TOKEN": ""}
        with patch.dict(os.environ, missing, clear=False):
            with self.assertRaisesRegex(ZohoMailConfigurationError, "ZOHO_MAIL_REFRESH_TOKEN"):
                ZohoMailAPIProvider()

        hostile = {**CONFIG, "ZOHO_ACCOUNTS_BASE_URL": "https://attacker.example"}
        with patch.dict(os.environ, hostile, clear=False):
            with self.assertRaisesRegex(ZohoMailConfigurationError, "documented HTTPS endpoint"):
                ZohoMailAPIProvider()

        malformed_port = {
            **CONFIG,
            "ZOHO_MAIL_API_BASE_URL": "https://mail.zoho.com:invalid/api",
        }
        with patch.dict(os.environ, malformed_port, clear=False):
            with self.assertRaisesRegex(ZohoMailConfigurationError, "documented HTTPS endpoint"):
                ZohoMailAPIProvider()

    async def test_provider_errors_are_generic_and_do_not_expose_zoho_response(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/oauth/v2/token":
                return httpx.Response(
                    200,
                    json={"access_token": "access-token", "expires_in": 3600},
                )
            return httpx.Response(
                403,
                json={"error": "sensitive-provider-detail"},
            )

        with patch.dict(os.environ, CONFIG, clear=False):
            provider = ZohoMailAPIProvider(transport=httpx.MockTransport(handler))
            with self.assertRaisesRegex(ZohoMailAPIError, "Zoho Mail delivery failed") as caught:
                await provider.send(envelope())

        self.assertNotIn("sensitive-provider-detail", str(caught.exception))

    def test_service_selects_and_caches_zoho_provider(self):
        with patch.dict(os.environ, CONFIG, clear=False):
            first = get_email_service()
            second = get_email_service()
        self.assertIs(first, second)
        self.assertIsInstance(first.provider, ZohoMailAPIProvider)


if __name__ == "__main__":
    unittest.main()
