import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.requests import Request
from starlette.responses import Response

from apps.api.security_headers import SecurityHeadersMiddleware
from packages.database.db import Base
from packages.database.models import AppUser, Tenant
from packages.database.schema import import_all_models
from packages.solutions import LifecycleStatus, SolutionService


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
