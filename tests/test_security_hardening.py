import os
import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from starlette.requests import Request

from apps.api.auth_cookies import csrf_secret_from_request, session_secret_from_request
from apps.api.public_safety import PublicEndpointSafetyMiddleware
from apps.api.security_headers import _unsafe_request_path, confined_file
from packages.kernel.contracts import CapabilityRisk, CapabilitySpec
from packages.mcp.oauth import (
    McpOAuthError,
    _secret,
    consume_refresh_token,
    grant_refresh_generation,
    issue_refresh_token,
)
from packages.plugins.egress_router import _canonical_egress_path, _path_grant_allows
from packages.workspace_modules.tools import _governed_computer_capabilities


def _request(path="/", *, raw_path=None, cookie=None):
    headers = []
    if cookie:
        headers.append((b"cookie", cookie.encode("latin-1")))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": raw_path if raw_path is not None else path.encode("latin-1"),
        "query_string": b"",
        "headers": headers,
        "client": ("203.0.113.10", 43120),
        "server": ("operly.example", 443),
    }
    return Request(scope)


def _spec(capability_id, *, risk=CapabilityRisk.LOW, reversible=True):
    return CapabilitySpec(
        id=capability_id,
        version="1.0.0",
        display_name=capability_id,
        description="test capability",
        provider_id="operly.test",
        scopes=frozenset({"workspace"}),
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        permissions=("computer:execute",),
        risk=risk,
        approval_required=False,
        resource_scope="workspace",
        reversible=reversible,
    )


class SecurityHardeningContractTests(unittest.TestCase):
    def test_path_traversal_rejected_before_catch_all_route(self):
        self.assertFalse(_unsafe_request_path(_request("/assets/app.js")))
        self.assertTrue(_unsafe_request_path(_request("/../../etc/passwd")))
        self.assertTrue(
            _unsafe_request_path(
                _request("/safe", raw_path=b"/%252e%252e%252fproc/self/environ")
            )
        )
        self.assertTrue(
            _unsafe_request_path(_request("/safe", raw_path=b"/%2e%2e%5cwindows%5csystem.ini"))
        )

    def test_frontend_file_sink_is_confined_even_through_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "dist"
            root.mkdir()
            inside = root / "asset.js"
            inside.write_text("inside", encoding="utf-8")
            outside = base / "secret.txt"
            outside.write_text("outside", encoding="utf-8")

            self.assertEqual(confined_file(root, "asset.js"), inside.resolve())
            self.assertIsNone(confined_file(root, "../secret.txt"))

            link = root / "escape.txt"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                pass
            else:
                self.assertIsNone(confined_file(root, "escape.txt"))

    def test_plugin_egress_paths_are_canonical_and_segment_bounded(self):
        self.assertEqual(_canonical_egress_path("/api/user/profile"), "/api/user/profile")
        self.assertTrue(_path_grant_allows("/api/user", ["/api/user"]))
        self.assertTrue(_path_grant_allows("/api/user/profile", ["/api/user"]))
        self.assertFalse(_path_grant_allows("/api/user-data", ["/api/user"]))
        for unsafe in (
            "/api/user/../admin",
            "/api/user/%2e%2e/admin",
            "/api/user/%252e%252e/admin",
            "//api/user",
            "/api\\user",
            "/api/user?next=/admin",
        ):
            with self.subTest(path=unsafe):
                with self.assertRaises(ValueError):
                    _canonical_egress_path(unsafe)

    def test_production_uses_only_host_prefixed_auth_cookies(self):
        with patch.dict(os.environ, {"OPERLY_ENV": "production"}, clear=False):
            request = _request(
                "/api/me",
                cookie=(
                    "operly_session=attacker-dev; __Host-operly_session=prod-session; "
                    "operly_csrf=attacker-csrf; __Host-operly_csrf=prod-csrf"
                ),
            )
            self.assertEqual(session_secret_from_request(request), "prod-session")
            self.assertEqual(csrf_secret_from_request(request), "prod-csrf")

            dev_only = _request(
                "/api/me",
                cookie="operly_session=attacker-dev; operly_csrf=attacker-csrf",
            )
            self.assertIsNone(session_secret_from_request(dev_only))
            self.assertIsNone(csrf_secret_from_request(dev_only))

    def test_arbitrary_computer_execution_requires_approval(self):
        hardened = {
            spec.id: spec
            for spec in _governed_computer_capabilities(
                (
                    _spec("computer.terminal.exec"),
                    _spec("computer.python.exec"),
                    _spec("computer.git.exec"),
                    _spec("computer.browser.evaluate"),
                    _spec("computer.files.write"),
                    _spec("computer.files.read", risk=CapabilityRisk.READ_ONLY),
                )
            )
        }
        for capability_id in (
            "computer.terminal.exec",
            "computer.python.exec",
            "computer.git.exec",
            "computer.browser.evaluate",
        ):
            spec = hardened[capability_id]
            self.assertEqual(spec.risk, CapabilityRisk.HIGH)
            self.assertTrue(spec.approval_required)
            self.assertFalse(spec.reversible)
        self.assertEqual(hardened["computer.files.write"].risk, CapabilityRisk.MEDIUM)
        self.assertFalse(hardened["computer.files.write"].reversible)
        self.assertEqual(hardened["computer.files.read"].risk, CapabilityRisk.READ_ONLY)

    def test_sandbox_network_hardening_has_no_swallowed_failure(self):
        source = Path("apps/sandbox_runner/server.mjs").read_text(encoding="utf-8")
        section = source.split("async function harden", 1)[1].split(
            "async function createSession", 1
        )[0]
        self.assertIn("requiredBootstrapExec", section)
        self.assertIn('"network-verify"', section)
        self.assertNotIn("catch {}", section)

    def test_production_requires_dedicated_mcp_signing_secret(self):
        with patch.dict(
            os.environ,
            {
                "OPERLY_ENV": "production",
                "AUTH_TOKEN_PEPPER": "x" * 48,
                "SESSION_SECRET": "y" * 48,
            },
            clear=True,
        ):
            with self.assertRaises(McpOAuthError):
                _secret()

        with patch.dict(
            os.environ,
            {"OPERLY_ENV": "production", "MCP_OAUTH_SECRET": "m" * 48},
            clear=True,
        ):
            self.assertEqual(_secret(), "m" * 48)

    def test_production_boot_validation_checks_mcp_secret_separation(self):
        source = Path("apps/api/main.py").read_text(encoding="utf-8")
        self.assertIn("A dedicated MCP token signing secret of at least 32 bytes is required", source)
        self.assertIn("MCP token signing secret must be distinct from session/auth secrets", source)
        self.assertIn("confined_file(WEB_DIST, route)", source)

    def test_github_actions_use_immutable_commit_shas(self):
        action_ref = re.compile(r"\buses:\s*[^@\s]+@([^\s#]+)")
        immutable_sha = re.compile(r"^[0-9a-f]{40}$")
        movable = []
        for workflow in sorted(Path(".github/workflows").glob("*.y*ml")):
            for line_number, line in enumerate(
                workflow.read_text(encoding="utf-8").splitlines(), start=1
            ):
                match = action_ref.search(line)
                if match and not immutable_sha.fullmatch(match.group(1)):
                    movable.append(f"{workflow}:{line_number}: {line.strip()}")
        self.assertEqual(
            movable,
            [],
            "Movable GitHub Action references are forbidden:\n" + "\n".join(movable),
        )


class SecurityHardeningAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_chunked_public_body_is_limited_while_streaming(self):
        app_reached_response = False

        async def app(scope, receive, send):
            nonlocal app_reached_response
            while True:
                message = await receive()
                if message.get("type") != "http.request" or not message.get("more_body"):
                    break
            app_reached_response = True
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = PublicEndpointSafetyMiddleware(app, max_body_bytes=5)

        async def never_rate_limited(scope, path):
            return False

        middleware._rate_limited = never_rate_limited
        messages = [
            {"type": "http.request", "body": b"1234", "more_body": True},
            {"type": "http.request", "body": b"56", "more_body": False},
        ]
        sent = []

        async def receive():
            return messages.pop(0)

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/api/public/webhooks/example",
            "raw_path": b"/api/public/webhooks/example",
            "query_string": b"",
            "headers": [(b"transfer-encoding", b"chunked")],
            "client": ("203.0.113.20", 50000),
            "server": ("operly.example", 443),
        }
        await middleware(scope, receive, send)
        starts = [message for message in sent if message["type"] == "http.response.start"]
        self.assertEqual(starts[0]["status"], 413)
        self.assertFalse(app_reached_response)

    async def test_mcp_refresh_token_replay_revokes_the_family(self):
        grant = SimpleNamespace(
            id="grant-1",
            client_id="chatgpt",
            status="active",
            tenant_id="workspace-1",
            principal_id="principal-1",
            expires_at=None,
            created_at=datetime(2026, 9, 1, 12, 0, 0),
            updated_at=datetime(2026, 9, 2, 12, 0, 0),
        )

        class FakeDb:
            def __init__(self):
                self.commits = 0

            async def scalar(self, statement):
                return grant

            async def flush(self):
                return None

            async def commit(self):
                self.commits += 1

        db = FakeDb()
        payload = {
            "grant_id": grant.id,
            "principal_id": grant.principal_id,
            "tenant_id": grant.tenant_id,
            "client_id": grant.client_id,
            "resource": "https://operly.example/mcp",
            "scopes": ["computer.*"],
            "refresh_generation": grant_refresh_generation(grant),
        }
        with patch.dict(
            os.environ,
            {"OPERLY_ENV": "development", "OPERLY_MCP_TOKEN_SECRET": "s" * 48},
            clear=True,
        ):
            token = issue_refresh_token(payload)
            first, first_grant = await consume_refresh_token(
                db,
                token,
                client_id="chatgpt",
                resource="https://operly.example/mcp",
            )
            self.assertEqual(first["grant_id"], grant.id)
            self.assertIs(first_grant, grant)
            with self.assertRaises(McpOAuthError):
                await consume_refresh_token(
                    db,
                    token,
                    client_id="chatgpt",
                    resource="https://operly.example/mcp",
                )
            self.assertEqual(grant.status, "revoked")
            self.assertEqual(db.commits, 1)


if __name__ == "__main__":
    unittest.main()
