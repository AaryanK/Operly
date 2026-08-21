import os
import unittest
from unittest.mock import patch

from packages.mcp.oauth import (
    McpOAuthError,
    consume_authorization_code,
    decode_access_token,
    decode_refresh_token,
    issue_access_token,
    issue_authorization_code,
    issue_refresh_token,
    pkce_s256,
)


class McpOAuthTokenTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"MCP_OAUTH_SECRET": "test-mcp-secret-that-is-long-and-random"}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_authorization_code_requires_matching_pkce_client_and_redirect(self):
        verifier = "a" * 64
        code = issue_authorization_code(
            grant_id="grant-1",
            principal_id="principal-1",
            tenant_id="tenant-1",
            client_id="chatgpt",
            redirect_uri="https://chatgpt.example/callback",
            scopes=["crm:read"],
            code_challenge=pkce_s256(verifier),
        )
        payload = consume_authorization_code(
            code,
            client_id="chatgpt",
            redirect_uri="https://chatgpt.example/callback",
            code_verifier=verifier,
        )
        self.assertEqual(payload["grant_id"], "grant-1")
        self.assertEqual(payload["scopes"], ["crm:read"])

        with self.assertRaisesRegex(McpOAuthError, "PKCE"):
            consume_authorization_code(
                code,
                client_id="chatgpt",
                redirect_uri="https://chatgpt.example/callback",
                code_verifier="wrong-verifier",
            )

        with self.assertRaisesRegex(McpOAuthError, "client mismatch"):
            consume_authorization_code(
                code,
                client_id="claude",
                redirect_uri="https://chatgpt.example/callback",
                code_verifier=verifier,
            )

    def test_access_and_refresh_tokens_are_type_separated(self):
        payload = {
            "grant_id": "grant-1",
            "principal_id": "principal-1",
            "tenant_id": "tenant-1",
            "client_id": "chatgpt",
            "scopes": ["crm:read", "crm:write"],
        }
        access = issue_access_token(payload)
        refresh = issue_refresh_token(payload)

        self.assertEqual(decode_access_token(access)["client_id"], "chatgpt")
        self.assertEqual(decode_refresh_token(refresh)["client_id"], "chatgpt")
        with self.assertRaises(McpOAuthError):
            decode_access_token(refresh)
        with self.assertRaises(McpOAuthError):
            decode_refresh_token(access)


if __name__ == "__main__":
    unittest.main()
