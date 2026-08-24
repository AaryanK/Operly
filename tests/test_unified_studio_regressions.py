import unittest
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.requests import Request
from starlette.responses import Response

from apps.api.security_headers import SecurityHeadersMiddleware
from packages.database.db import Base
from packages.database.models import AppUser, Tenant
from packages.database.schema import import_all_models
from packages.database.studio_models import StudioProject
from packages.database.studio_source_models import StudioAgentRun
from packages.solutions import LifecycleStatus, SolutionService
from packages.studio.agent_runs import run_json


ROOT = Path(__file__).resolve().parents[1]


class UnifiedStudioRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.user = AppUser(email="studio-regression@example.com", password_hash="x")
        self.tenant = Tenant(name="Studio Regression")
        self.db.add_all([self.user, self.tenant])
        await self.db.flush()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def test_named_website_can_be_created_without_company_profile(self):
        solution = await SolutionService().create_presence(
            self.db,
            self.tenant.id,
            self.user.id,
            "First Website",
        )
        self.assertEqual(solution.name, "First Website")
        self.assertEqual(solution.lifecycle_status, LifecycleStatus.PREVIEW_READY)
        self.assertEqual(solution.preview_state, "ready")

    async def test_studio_run_json_always_exposes_polling_identity_and_state(self):
        project = StudioProject(
            tenant_id=self.tenant.id,
            name="Run contract",
            slug="run-contract",
            created_by=self.user.id,
        )
        self.db.add(project)
        await self.db.flush()
        run = StudioAgentRun(
            tenant_id=self.tenant.id,
            project_id=project.id,
            operation="edit",
            instruction="fix it",
            state="queued",
            created_by=self.user.id,
        )
        self.db.add(run)
        await self.db.flush()

        payload = await run_json(self.db, run)

        self.assertEqual(payload["id"], run.id)
        self.assertEqual(payload["state"], "queued")
        self.assertEqual(payload["operation"], "edit")
        self.assertIsInstance(payload["events"], list)

    def test_browser_bridge_recovers_empty_studio_run_responses(self):
        source = (ROOT / "apps" / "web" / "static" / "studio-ui-bridge.js").read_text("utf-8")

        self.assertIn("studioRunSafeApi", source)
        self.assertIn("/source/runs/latest", source)
        self.assertIn("latest.id && latest.state", source)
        self.assertIn("Studio source agent did not return a usable run record", source)

    def test_logout_requires_confirmed_server_logout_before_returning_to_login(self):
        # Main retired the old operly-modern.js overlay. auth.js is now the
        # canonical session owner, so keep the regression pinned to the code that
        # actually performs logout instead of a deleted compatibility file.
        source = (ROOT / "apps" / "web" / "static" / "auth.js").read_text("utf-8")

        request = 'await api("/auth/logout", { method: "POST", body: "{}" });'
        clear = "state.me = null;"
        navigate = 'navigate("/login");'
        self.assertIn(request, source)
        self.assertIn(clear, source)
        self.assertIn(navigate, source)
        self.assertLess(source.index(request), source.index(clear, source.index(request)))
        self.assertLess(source.index(clear, source.index(request)), source.index(navigate, source.index(request)))
        self.assertIn("We couldn't sign you out", source)
        self.assertNotIn('finally { location.assign("/login")', source)

    async def test_solution_preview_redirect_is_same_origin_frameable(self):
        middleware = SecurityHeadersMiddleware(lambda scope, receive, send: None)

        async def call_next(_request):
            return Response(status_code=307)

        request = Request({
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/api/solutions/example/preview",
            "raw_path": b"/api/solutions/example/preview",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 443),
        })
        response = await middleware.dispatch(request, call_next)
        self.assertEqual(response.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertIn("frame-ancestors 'self'", response.headers["Content-Security-Policy"])

        normal = Request({
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/api/me",
            "raw_path": b"/api/me",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 443),
        })
        normal_response = await middleware.dispatch(normal, call_next)
        self.assertEqual(normal_response.headers["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", normal_response.headers["Content-Security-Policy"])
