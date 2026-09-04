import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from apps.api.security_headers import SecurityHeadersMiddleware
from packages.kernel.approvals import (
    ApprovalError,
    arguments_hash,
    claim_approved_invocation,
)
from packages.kernel.contracts import CapabilityRisk, RuntimeRequest
from packages.kernel.idempotency import reserve_request
from packages.workspace_modules.agent_computer.router import _governed_native_contracts
from packages.workspace_modules.studio.router import _enforce_content_origin


def _request(path: str, *, host: str) -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": b"",
        "headers": [(b"host", host.encode("ascii"))],
        "client": ("203.0.113.10", 50000),
        "server": (host, 443),
    }
    return Request(scope)


class _FlushDb:
    def __init__(self):
        self.flushes = 0

    async def flush(self):
        self.flushes += 1


class _ReservationDb:
    def __init__(self):
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0

    async def scalar(self, statement):
        del statement
        return None

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class PreAgentRuntimeSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_approval_claim_is_bound_to_original_request_id(self):
        args = {"resource_id": "dangerous-write"}
        row = SimpleNamespace(
            status="approved",
            requested_by_principal_id="principal-1",
            capability_id="test.write",
            arguments_hash=arguments_hash("test.write", args),
            request_id="request-original",
        )
        db = _FlushDb()
        context = SimpleNamespace(principal_id="principal-1")

        with patch(
            "packages.kernel.approvals.approval_for_context",
            new=AsyncMock(return_value=row),
        ):
            with self.assertRaises(ApprovalError):
                await claim_approved_invocation(
                    db,
                    context=context,
                    approval_id="approval-1",
                    request_id="request-different",
                    capability_id="test.write",
                    arguments=args,
                )
            self.assertEqual(row.status, "approved")

            claimed = await claim_approved_invocation(
                db,
                context=context,
                approval_id="approval-1",
                request_id="request-original",
                capability_id="test.write",
                arguments=args,
            )

        self.assertIs(claimed, row)
        self.assertEqual(row.status, "executing")
        self.assertEqual(db.flushes, 1)

    async def test_mutation_reservation_and_approval_claim_commit_before_provider(self):
        db = _ReservationDb()
        context = SimpleNamespace(
            is_personal=False,
            workspace_id="workspace-1",
            user_id="user-1",
            principal_id="principal-1",
            scope_kind=SimpleNamespace(value="workspace"),
        )
        request = RuntimeRequest(
            capability_id="test.write",
            arguments={"value": 1},
            request_id="request-1",
            approval_id="approval-1",
        )
        approval = SimpleNamespace(capability_id="test.write")
        claim_mock = AsyncMock(return_value=approval)

        with patch(
            "packages.kernel.idempotency.approval_for_context",
            new=AsyncMock(return_value=approval),
        ), patch(
            "packages.kernel.idempotency.claim_approved_invocation",
            new=claim_mock,
        ):
            reservation = await reserve_request(
                db,
                context=context,
                request=request,
                run_id="run-1",
            )

        self.assertIsNotNone(reservation.claim)
        self.assertEqual(reservation.claim.status, "running")
        self.assertEqual(db.commits, 1)
        claim_mock.assert_awaited_once()
        self.assertEqual(claim_mock.await_args.kwargs["request_id"], "request-1")

    async def test_studio_bytes_never_serve_from_authenticated_origin_in_production(self):
        with patch.dict(
            os.environ,
            {
                "OPERLY_ENV": "production",
                "OPERLY_STUDIO_PUBLIC_HOST": "operlyusercontent.example.net",
            },
            clear=False,
        ):
            redirect = _enforce_content_origin(
                _request("/studio-sites/solution-1/", host="operly.example.com")
            )
            self.assertIsNotNone(redirect)
            self.assertEqual(redirect.status_code, 307)
            self.assertEqual(
                redirect.headers["location"],
                "https://operlyusercontent.example.net/studio-sites/solution-1/",
            )
            self.assertIsNone(
                _enforce_content_origin(
                    _request(
                        "/studio-sites/solution-1/",
                        host="operlyusercontent.example.net",
                    )
                )
            )

        with patch.dict(
            os.environ,
            {"OPERLY_ENV": "production", "OPERLY_STUDIO_PUBLIC_HOST": ""},
            clear=False,
        ):
            with self.assertRaises(HTTPException) as raised:
                _enforce_content_origin(
                    _request("/studio-sites/solution-1/", host="operly.example.com")
                )
            self.assertEqual(raised.exception.status_code, 503)

    async def test_studio_content_host_cannot_serve_operly_api(self):
        reached = False

        async def app(scope, receive, send):
            del scope, receive, send

        async def call_next(request):
            nonlocal reached
            del request
            reached = True
            return PlainTextResponse("unexpected")

        middleware = SecurityHeadersMiddleware(app)
        with patch.dict(
            os.environ,
            {"OPERLY_STUDIO_PUBLIC_HOST": "operlyusercontent.example.net"},
            clear=False,
        ):
            response = await middleware.dispatch(
                _request("/api/me", host="operlyusercontent.example.net"),
                call_next,
            )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(reached)

    async def test_agent_computer_metadata_matches_governed_risk(self):
        specs = {spec.id: spec for spec in _governed_native_contracts()}

        for capability_id in (
            "computer.terminal.exec",
            "computer.python.exec",
            "computer.git.exec",
            "computer.browser.evaluate",
        ):
            spec = specs[capability_id]
            self.assertEqual(spec.risk, CapabilityRisk.HIGH)
            self.assertTrue(spec.approval_required)
            self.assertFalse(spec.reversible)

        self.assertEqual(specs["computer.files.write"].risk, CapabilityRisk.MEDIUM)
        self.assertFalse(specs["computer.files.write"].reversible)
        self.assertEqual(specs["computer.files.read"].risk, CapabilityRisk.READ_ONLY)


class PreAgentRuntimeStaticRegressionTests(unittest.TestCase):
    def test_production_boot_requires_separate_studio_cookie_site(self):
        source = open("apps/api/main.py", encoding="utf-8").read()
        self.assertIn("OPERLY_STUDIO_PUBLIC_HOST", source)
        self.assertIn("_site_suffix_hint(STUDIO_PUBLIC_HOST)", source)
        self.assertIn(
            "Studio published content must use a separate registrable-style origin",
            source,
        )
        self.assertIn('"ai_runtime_enabled": False', source)


if __name__ == "__main__":
    unittest.main()
