"""Local Operly user issuance is forbidden after DragonZpyder identity cutover."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from apps.api.identity_owner_gate import deny_local_identity_route, identity_owner_middleware


class DragonZpyderIdentityOwnerTests(unittest.TestCase):
    def test_legacy_account_routes_blocked_in_strict_mode(self):
        with patch.dict("os.environ", {"OPERLY_IDENTITY_OWNER": "dragonzpyder"}):
            for path in [
                "/api/auth/signup", "/api/auth/login", "/api/session/login",
                "/api/auth/google", "/api/auth/verify-email",
                "/api/auth/forgot-password", "/api/auth/reset-password",
                "/api/identities/discord/sign-in", "/api/identities/discord/callback",
            ]:
                self.assertTrue(deny_local_identity_route(path), path)
            for route in [
                "/api/auth/dragonzpyder/start",
                "/api/auth/dragonzpyder/callback",
                "/api/agent/chat", "/mcp", "/api/auth/workspaces",
            ]:
                self.assertFalse(deny_local_identity_route(route), route)

    def test_local_routes_untouched_pre_cutover(self):
        with patch.dict("os.environ", {"OPERLY_IDENTITY_OWNER": "operly"}):
            self.assertFalse(deny_local_identity_route("/api/auth/signup"))

    def test_strict_middleware_never_reaches_account_creation(self):
        called = []
        async def next_handler(request):
            called.append(request.url.path)
            return "ok"

        request = SimpleNamespace(url=SimpleNamespace(path="/api/auth/google"))
        with patch.dict("os.environ", {"OPERLY_IDENTITY_OWNER": "dragonzpyder"}):
            blocked = asyncio.run(identity_owner_middleware(request, next_handler))
            self.assertEqual(blocked.status_code, 410)
            self.assertEqual(called, [])
            request.url.path = "/api/auth/dragonzpyder/start"
            self.assertEqual(asyncio.run(identity_owner_middleware(request, next_handler)), "ok")
            self.assertEqual(called, ["/api/auth/dragonzpyder/start"])


if __name__ == "__main__":
    unittest.main()
