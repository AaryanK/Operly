import asyncio
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.csrf import CSRFMiddleware
from apps.api.dependencies import AuthContext, get_auth_context, get_db
from apps.api.google_auth import GoogleAuthenticationError, GoogleIdentityClaims
from apps.api.request_safety import AuthRequestSafetyMiddleware
from apps.api.security import legacy_hash_password, verify_password
from apps.api.session import router
from packages.database.db import Base
from packages.database.models import (
    AppUser,
    AuthChallenge,
    AuthIdentity,
    AuthSession,
    SecurityEvent,
    Tenant,
    TenantMember,
    Integration,
)
from packages.email.providers.memory import MemoryEmailProvider
from packages.email.service import EmailService, set_email_service_for_tests


class AuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "OPERLY_ENV": "test",
                "PUBLIC_BASE_URL": "http://testserver",
                "SESSION_SECRET": "test-session-secret-that-is-long-enough",
                "AUTH_TOKEN_PEPPER": "test-token-pepper-that-is-long-enough",
            },
        )
        self.environment.start()
        self.tmp = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp.name) / "auth.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        self.provider = MemoryEmailProvider()
        set_email_service_for_tests(EmailService(self.provider))

        app = FastAPI()
        app.include_router(router)
        app.add_middleware(CSRFMiddleware)
        app.add_middleware(AuthRequestSafetyMiddleware)

        async def override_db():
            async with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = override_db

        @app.get("/api/protected")
        async def protected(auth: AuthContext = Depends(get_auth_context)):
            return {
                "user_id": auth.user.id,
                "tenant_id": auth.tenant.id,
                "role": auth.role,
                "session_id": auth.session.id,
            }

        import apps.api.csrf as csrf_module
        import apps.api.session as session_module

        self.old_csrf_factory = csrf_module.SessionFactory
        self.old_session_factory = session_module.SessionFactory
        csrf_module.SessionFactory = self.sessions
        session_module.SessionFactory = self.sessions
        self.csrf_module = csrf_module
        self.session_module = session_module
        self.app = app
        self.client = await self.new_client()

    async def asyncTearDown(self):
        await self.client.aclose()
        self.csrf_module.SessionFactory = self.old_csrf_factory
        self.session_module.SessionFactory = self.old_session_factory
        set_email_service_for_tests(None)
        await self.engine.dispose()
        self.tmp.cleanup()
        self.environment.stop()

    async def new_client(self, base_url: str = "http://testserver"):
        client = AsyncClient(transport=ASGITransport(app=self.app), base_url=base_url)
        await client.get("/api/auth/bootstrap")
        return client

    @staticmethod
    def cookie(client: AsyncClient, *names: str) -> str:
        for cookie in client.cookies.jar:
            if cookie.name in names:
                return cookie.value
        return ""

    def csrf(self, client: AsyncClient | None = None) -> str:
        current = client or self.client
        return self.cookie(
            current,
            "__Host-operly_csrf",
            "operly_csrf",
            "operly_preauth_csrf",
        )

    async def post(self, path: str, payload: dict, *, client: AsyncClient | None = None, csrf: str | None = None):
        current = client or self.client
        token = self.csrf(current) if csrf is None else csrf
        headers = {"X-CSRF-Token": token} if token else {}
        return await current.post(path, json=payload, headers=headers)

    def message(self, subject_prefix: str):
        return next(message for message in reversed(self.provider.messages) if message.subject.startswith(subject_prefix))

    async def signup(self, email="owner@example.com", password="correct horse battery staple", name="Owner", *, client=None):
        return await self.post(
            "/api/auth/signup",
            {"display_name": name, "email": email, "password": password},
            client=client,
        )

    async def verify_signup(self, signup_response, *, client=None):
        code = re.search(r"verification code is (\d{6})", self.message("Verify").text_body).group(1)
        return await self.post(
            "/api/auth/verify-email",
            {"challenge_id": signup_response.json()["challenge_id"], "code": code},
            client=client,
        )

    async def established_account(self, email="owner@example.com", password="correct horse battery staple", *, client=None):
        response = await self.signup(email, password, client=client)
        self.assertEqual(response.status_code, 201, response.text)
        verified = await self.verify_signup(response, client=client)
        self.assertEqual(verified.status_code, 200, verified.text)
        return response

    async def established_workspace(self, email="owner@example.com", password="correct horse battery staple", *, client=None):
        await self.established_account(email, password, client=client)
        async with self.sessions() as db:
            user = await db.scalar(select(AppUser).where(AppUser.email == email.strip().lower()))
            tenant = Tenant(name="Explicit test workspace")
            db.add(tenant)
            await db.flush()
            db.add(TenantMember(tenant_id=tenant.id, user_id=user.id, role="owner"))
            await db.commit()
            tenant_id = tenant.id
        switched = await self.post(
            "/api/auth/switch-workspace",
            {"tenant_id": tenant_id},
            client=client,
        )
        self.assertEqual(switched.status_code, 200, switched.text)
        return tenant_id

    async def test_signup_is_atomic_normalized_and_owner_scoped(self):
        response = await self.signup("  Owner@Example.COM  ", name="  Alice   Owner ")
        self.assertEqual(response.status_code, 201, response.text)
        async with self.sessions() as db:
            user = await db.scalar(select(AppUser).where(AppUser.email == "owner@example.com"))
            self.assertEqual(user.display_name, "Alice Owner")
            self.assertIsNone(user.email_verified_at)
            self.assertTrue(user.password_hash.startswith("$argon2id$"))
            memberships = (await db.scalars(select(TenantMember).where(TenantMember.user_id == user.id))).all()
            self.assertEqual(len(memberships), 0)
            self.assertEqual(await db.scalar(select(func.count(Tenant.id))), 0)
            identity = await db.scalar(select(AuthIdentity).where(AuthIdentity.user_id == user.id))
            self.assertEqual(identity.provider, "password")

        duplicate = await self.signup("owner@EXAMPLE.com")
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()["detail"]["code"], "ACCOUNT_PENDING_VERIFICATION")
        async with self.sessions() as db:
            self.assertEqual(await db.scalar(select(func.count(AppUser.id))), 1)
            self.assertEqual(await db.scalar(select(func.count(Tenant.id))), 0)

    async def test_concurrent_case_variant_signup_creates_one_account(self):
        other = await self.new_client()
        try:
            first, second = await asyncio.gather(
                self.signup("race@example.com", client=self.client),
                self.signup("RACE@EXAMPLE.COM", client=other),
            )
            self.assertEqual(sorted([first.status_code, second.status_code]), [201, 409])
            async with self.sessions() as db:
                self.assertEqual(await db.scalar(select(func.count(AppUser.id))), 1)
                self.assertEqual(await db.scalar(select(func.count(Tenant.id))), 0)
        finally:
            await other.aclose()

    async def test_signup_validation_and_email_failure_are_truthful(self):
        invalid = await self.signup("not-an-email")
        self.assertEqual(invalid.status_code, 422)
        weak = await self.signup("weak@example.com", "password123")
        self.assertEqual(weak.status_code, 422)
        oversized = await self.post(
            "/api/auth/signup",
            {"display_name": "Owner", "email": "x" * 321, "password": "a valid long password phrase"},
        )
        self.assertEqual(oversized.status_code, 422)

        self.provider.fail = True
        failed = await self.signup("delivery@example.com")
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(failed.json()["detail"]["code"], "EMAIL_DELIVERY_FAILED")
        duplicate = await self.signup("delivery@example.com")
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()["detail"]["code"], "ACCOUNT_PENDING_VERIFICATION")
        async with self.sessions() as db:
            user = await db.scalar(select(AppUser).where(AppUser.email == "delivery@example.com"))
            challenge = await db.scalar(select(AuthChallenge).where(AuthChallenge.user_id == user.id))
            self.assertEqual(challenge.delivery_status, "failed")
            self.assertEqual(await db.scalar(select(func.count(TenantMember.id))), 0)

    async def test_auth_request_contract_rejects_malformed_duplicate_extra_and_method_confusion(self):
        csrf = self.csrf()
        malformed = await self.client.post(
            "/api/auth/login",
            content=b'{"email":',
            headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
        )
        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(malformed.json()["detail"]["code"], "INVALID_JSON")
        duplicate = await self.client.post(
            "/api/auth/login",
            content=b'{"email":"one@example.com","email":"two@example.com","password":"anything"}',
            headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(duplicate.json()["detail"]["code"], "DUPLICATE_JSON_FIELD")
        wrong_type = await self.client.post(
            "/api/auth/login",
            content="email=x",
            headers={"Content-Type": "application/x-www-form-urlencoded", "X-CSRF-Token": csrf},
        )
        self.assertEqual(wrong_type.status_code, 415)
        extra = await self.post(
            "/api/auth/signup",
            {
                "display_name": "<script>alert(1)</script>",
                "email": "payload@example.com",
                "password": "a safe and long password",
                "role": "admin",
                "tenant_id": "attacker",
            },
        )
        self.assertEqual(extra.status_code, 422)
        huge = await self.client.post(
            "/api/auth/login",
            content=b"{" + (b"x" * (21 * 1024)) + b"}",
            headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
        )
        self.assertEqual(huge.status_code, 413)
        method = await self.client.get("/api/auth/login")
        self.assertEqual(method.status_code, 405)

    async def test_verification_code_link_expiry_attempt_cap_resend_and_replay(self):
        signup = await self.signup()
        code = re.search(r"verification code is (\d{6})", self.message("Verify").text_body).group(1)
        for _ in range(5):
            wrong = await self.post(
                "/api/auth/verify-email",
                {"challenge_id": signup.json()["challenge_id"], "code": "111111"},
            )
            self.assertEqual(wrong.status_code, 400)
        capped = await self.post(
            "/api/auth/verify-email",
            {"challenge_id": signup.json()["challenge_id"], "code": "111111"},
        )
        self.assertEqual(capped.status_code, 400)
        locked = await self.post(
            "/api/auth/verify-email",
            {"challenge_id": signup.json()["challenge_id"], "code": code},
        )
        self.assertEqual(locked.status_code, 409)

        resent = await self.post("/api/auth/resend-verification", {"email": "owner@example.com"})
        self.assertEqual(resent.status_code, 200)
        old = await self.post(
            "/api/auth/verify-email",
            {"challenge_id": signup.json()["challenge_id"], "code": code},
        )
        self.assertEqual(old.status_code, 409)
        new_code = re.search(r"verification code is (\d{6})", self.message("Verify").text_body).group(1)
        verified = await self.post(
            "/api/auth/verify-email",
            {"challenge_id": resent.json()["challenge_id"], "code": new_code},
        )
        self.assertEqual(verified.status_code, 200)
        replay = await self.post(
            "/api/auth/verify-email",
            {"challenge_id": resent.json()["challenge_id"], "code": new_code},
        )
        self.assertEqual(replay.status_code, 409)

        expired_signup = await self.signup("expired@example.com")
        async with self.sessions() as db:
            challenge = await db.get(AuthChallenge, expired_signup.json()["challenge_id"])
            challenge.expires_at = datetime.utcnow() - timedelta(seconds=1)
            await db.commit()
        expired = await self.post(
            "/api/auth/verify-email",
            {"challenge_id": expired_signup.json()["challenge_id"], "code": "123456"},
        )
        self.assertEqual(expired.status_code, 410)

    async def test_verification_link_is_single_use(self):
        signup = await self.signup()
        token = re.search(r"verify-email#token=(\S+)", self.message("Verify").text_body).group(1)
        verified = await self.post("/api/auth/verify-email", {"token": token})
        self.assertEqual(verified.status_code, 200)
        replay = await self.post("/api/auth/verify-email", {"token": token})
        self.assertEqual(replay.status_code, 409)

    async def test_login_migrates_legacy_password_and_prevents_fixation(self):
        password = "legacy correct horse phrase"
        async with self.sessions() as db:
            tenant = Tenant(name="Legacy")
            user = AppUser(
                email="legacy@example.com",
                password_hash=legacy_hash_password(password),
                email_verified_at=datetime.utcnow(),
            )
            db.add_all([tenant, user])
            await db.flush()
            db.add(TenantMember(tenant_id=tenant.id, user_id=user.id, role="owner"))
            await db.commit()

        self.client.cookies.set("operly_session", "attacker-fixed-value", domain="testserver.local", path="/")
        result = await self.post(
            "/api/auth/login",
            {"email": "legacy@example.com", "password": password},
            csrf=self.cookie(self.client, "operly_preauth_csrf"),
        )
        self.assertEqual(result.status_code, 200, result.text)
        self.assertNotEqual(self.cookie(self.client, "operly_session"), "attacker-fixed-value")
        async with self.sessions() as db:
            user = await db.scalar(select(AppUser).where(AppUser.email == "legacy@example.com"))
            self.assertTrue(user.password_hash.startswith("$argon2id$"))
            self.assertTrue(verify_password(password, user.password_hash))

    async def test_login_rejects_wrong_inactive_unverified_and_rate_limits(self):
        signup = await self.signup()
        unverified = await self.post(
            "/api/auth/login",
            {"email": "owner@example.com", "password": "correct horse battery staple"},
        )
        self.assertEqual(unverified.status_code, 403)
        await self.verify_signup(signup)
        await self.post("/api/auth/logout", {})
        await self.client.get("/api/auth/bootstrap")

        wrong = await self.post(
            "/api/auth/login",
            {"email": "owner@example.com", "password": "wrong"},
        )
        self.assertEqual(wrong.status_code, 401)
        async with self.sessions() as db:
            user = await db.scalar(select(AppUser).where(AppUser.email == "owner@example.com"))
            user.active = False
            await db.commit()
        inactive = await self.post(
            "/api/auth/login",
            {"email": "owner@example.com", "password": "correct horse battery staple"},
        )
        self.assertEqual(inactive.status_code, 401)
        for _ in range(6):
            limited = await self.post(
                "/api/auth/login",
                {"email": "owner@example.com", "password": "wrong"},
            )
        self.assertEqual(limited.status_code, 429)
        self.assertIn("retry-after", limited.headers)

    async def test_csrf_missing_cross_session_cross_site_and_revoked_session(self):
        await self.established_account()
        missing = await self.client.post("/api/auth/logout", json={})
        self.assertEqual(missing.status_code, 403)

        second = await self.new_client()
        try:
            logged_in = await self.post(
                "/api/auth/login",
                {"email": "owner@example.com", "password": "correct horse battery staple"},
                client=second,
            )
            self.assertEqual(logged_in.status_code, 200)
            other_csrf = self.cookie(self.client, "operly_csrf")
            second.cookies.set("operly_csrf", other_csrf, domain="testserver.local", path="/")
            cross_session = await self.post("/api/auth/logout", {}, client=second, csrf=other_csrf)
            self.assertEqual(cross_session.status_code, 403)
        finally:
            await second.aclose()

        cross_site = await self.client.post(
            "/api/auth/logout",
            json={},
            headers={"X-CSRF-Token": self.csrf(), "Origin": "https://attacker.example"},
        )
        self.assertEqual(cross_site.status_code, 403)
        valid = await self.post("/api/auth/logout", {})
        self.assertEqual(valid.status_code, 200)
        rejected = await self.client.get("/api/protected")
        self.assertEqual(rejected.status_code, 401)

    async def test_logout_all_and_session_inventory(self):
        await self.established_account()
        second = await self.new_client()
        try:
            await self.post(
                "/api/auth/login",
                {"email": "owner@example.com", "password": "correct horse battery staple"},
                client=second,
            )
            inventory = await self.client.get("/api/auth/sessions")
            self.assertEqual(inventory.status_code, 200)
            self.assertEqual(len(inventory.json()), 2)
            self.assertEqual(sum(item["current"] for item in inventory.json()), 1)
            revoked = await self.post("/api/auth/logout-all", {})
            self.assertEqual(revoked.status_code, 200)
            self.assertEqual(revoked.json()["revoked"], 2)
            self.assertEqual((await second.get("/api/protected")).status_code, 401)
        finally:
            await second.aclose()

    async def test_password_reset_is_generic_single_use_and_revokes_old_sessions(self):
        await self.established_account()
        old_session = self.cookie(self.client, "operly_session")
        known = await self.post("/api/auth/forgot-password", {"email": "owner@example.com"})
        await self.client.get("/api/auth/bootstrap")
        unknown = await self.post("/api/auth/forgot-password", {"email": "missing@example.com"})
        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known.json(), unknown.json())

        reset_message = self.message("Reset")
        token = re.search(r"reset-password#token=(\S+)", reset_message.text_body).group(1)
        reset = await self.post(
            "/api/auth/reset-password",
            {"token": token, "password": "an entirely new battery phrase"},
        )
        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertNotEqual(self.cookie(self.client, "operly_session"), old_session)
        async with self.sessions() as db:
            user = await db.scalar(select(AppUser).where(AppUser.email == "owner@example.com"))
            self.assertTrue(verify_password("an entirely new battery phrase", user.password_hash))
            self.assertFalse(verify_password("correct horse battery staple", user.password_hash))
            rows = (await db.scalars(select(AuthSession).where(AuthSession.user_id == user.id))).all()
            self.assertEqual(sum(row.revoked_at is None for row in rows), 1)
        replay = await self.post(
            "/api/auth/reset-password",
            {"token": token, "password": "another entirely new phrase"},
        )
        self.assertEqual(replay.status_code, 409)
        self.assertTrue(any(message.subject.startswith("Your OPERLY password") for message in self.provider.messages))

    async def test_password_reset_code_and_expired_challenge(self):
        await self.established_account()
        await self.post("/api/auth/forgot-password", {"email": "owner@example.com"})
        reset_message = self.message("Reset")
        code = re.search(r"reset code is (\d{6})", reset_message.text_body).group(1)
        reset = await self.post(
            "/api/auth/reset-password",
            {
                "email": "owner@example.com",
                "code": code,
                "password": "new password by emailed code",
            },
        )
        self.assertEqual(reset.status_code, 200, reset.text)

        await self.client.get("/api/auth/bootstrap")
        await self.post("/api/auth/forgot-password", {"email": "owner@example.com"})
        reset_message = self.message("Reset")
        token = re.search(r"reset-password#token=(\S+)", reset_message.text_body).group(1)
        async with self.sessions() as db:
            challenge = await db.scalar(
                select(AuthChallenge)
                .where(AuthChallenge.purpose == "password_reset")
                .order_by(AuthChallenge.created_at.desc())
            )
            challenge.expires_at = datetime.utcnow() - timedelta(seconds=1)
            await db.commit()
        expired = await self.post(
            "/api/auth/reset-password",
            {"token": token, "password": "another new password phrase"},
        )
        self.assertEqual(expired.status_code, 410)

    async def test_google_new_returning_replay_and_linking_takeover_defense(self):
        claims = GoogleIdentityClaims(
            subject="google-subject-1",
            email="google@example.com",
            display_name="Google Owner",
            expires_at=int((datetime.utcnow() + timedelta(minutes=10)).timestamp()),
            raw={},
        )
        credential = "a" * 200
        with patch.object(self.session_module, "verify_google_credential", return_value=claims):
            first = await self.post("/api/auth/google", {"credential": credential})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertTrue(first.json()["new_account"])
        await self.client.get("/api/auth/bootstrap")
        with patch.object(self.session_module, "verify_google_credential", return_value=claims):
            replay = await self.post("/api/auth/google", {"credential": credential})
        self.assertEqual(replay.status_code, 409)

        returning = await self.new_client()
        try:
            with patch.object(self.session_module, "verify_google_credential", return_value=claims):
                result = await self.post("/api/auth/google", {"credential": "b" * 200}, client=returning)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertFalse(result.json()["new_account"])
        finally:
            await returning.aclose()

        attack_client = await self.new_client()
        try:
            async with self.sessions() as db:
                user = AppUser(email="unverified@example.com", password_hash=legacy_hash_password("valid legacy phrase"))
                db.add(user)
                await db.commit()
            attack_claims = GoogleIdentityClaims(
                subject="attacker-subject",
                email="unverified@example.com",
                display_name="Attacker",
                expires_at=int((datetime.utcnow() + timedelta(minutes=10)).timestamp()),
                raw={},
            )
            with patch.object(self.session_module, "verify_google_credential", return_value=attack_claims):
                blocked = await self.post("/api/auth/google", {"credential": "c" * 200}, client=attack_client)
            self.assertEqual(blocked.status_code, 409)
            async with self.sessions() as db:
                identity = await db.scalar(
                    select(AuthIdentity).where(
                        AuthIdentity.provider == "google",
                        AuthIdentity.provider_subject == "attacker-subject",
                    )
                )
                self.assertIsNone(identity)
        finally:
            await attack_client.aclose()

    async def test_google_validation_failure_is_audited_without_credential(self):
        with patch.object(
            self.session_module,
            "verify_google_credential",
            side_effect=GoogleAuthenticationError("Google could not confirm this sign-in"),
        ):
            result = await self.post("/api/auth/google", {"credential": "x" * 200})
        self.assertEqual(result.status_code, 401)
        async with self.sessions() as db:
            event = await db.scalar(
                select(SecurityEvent)
                .where(SecurityEvent.event_type == "google_authentication_failure")
                .order_by(SecurityEvent.created_at.desc())
            )
            self.assertNotIn("x" * 20, event.metadata_json)

    async def test_other_tenant_cannot_be_selected(self):
        current_id = await self.established_workspace()
        async with self.sessions() as db:
            other = Tenant(name="Other tenant")
            db.add(other)
            await db.commit()
            other_id = other.id
        blocked = await self.post("/api/auth/switch-workspace", {"tenant_id": other_id})
        self.assertEqual(blocked.status_code, 404)
        protected = await self.client.get("/api/protected")
        self.assertEqual(protected.status_code, 200, protected.text)
        self.assertEqual(protected.json()["tenant_id"], current_id)
        self.assertNotEqual(protected.json()["tenant_id"], other_id)

    async def test_authenticated_tenant_cannot_read_other_domain_resources(self):
        from packages.database.application_builder_models import ManagedApplication
        from packages.database.company_models import BusinessEventRecord
        from packages.database.custom_software_models import (
            GeneratedProject,
            RunnerBuildRecord,
            RunnerPreviewRecord,
        )
        from packages.database.product_models import (
            CompanyProfile,
            SolutionDeployment,
            SolutionJob,
            SolutionRecord,
        )
        from packages.database.studio_models import StudioDeployment, StudioProject

        async with self.engine.begin() as connection:
            for table in (
                CompanyProfile.__table__,
                BusinessEventRecord.__table__,
                StudioProject.__table__,
                StudioDeployment.__table__,
                ManagedApplication.__table__,
                GeneratedProject.__table__,
                RunnerBuildRecord.__table__,
                RunnerPreviewRecord.__table__,
                SolutionRecord.__table__,
                SolutionJob.__table__,
                SolutionDeployment.__table__,
            ):
                await connection.run_sync(lambda bind, current=table: current.create(bind, checkfirst=True))

        await self.established_workspace()
        current = (await self.client.get("/api/protected")).json()
        async with self.sessions() as db:
            other = Tenant(name="Other protected tenant")
            db.add(other)
            await db.flush()
            db.add_all(
                [
                    CompanyProfile(
                        tenant_id=current["tenant_id"],
                        profile_json='{"marker":"tenant-a-profile"}',
                    ),
                    CompanyProfile(
                        tenant_id=other.id,
                        profile_json='{"marker":"tenant-b-secret-profile"}',
                    ),
                    Integration(
                        tenant_id=other.id,
                        provider="whatsapp",
                        status="connected",
                    ),
                    BusinessEventRecord(
                        tenant_id=other.id,
                        event_type="tenant-b-secret-event",
                        payload_json='{"secret":"tenant-b-activity"}',
                    ),
                ]
            )
            solution = SolutionRecord(
                tenant_id=other.id,
                name="Tenant B secret solution",
                description="not visible to tenant A",
                solution_type="digital_presence",
                lifecycle_status="draft",
                runtime_type="studio",
                runtime_reference="tenant-b-runtime",
            )
            db.add(solution)
            await db.flush()
            db.add(
                SolutionJob(
                    tenant_id=other.id,
                    solution_id=solution.id,
                    source_version_reference="v1",
                    job_type="build",
                    status="queued",
                    idempotency_key="tenant-b-secret-job",
                )
            )
            await db.commit()
            other_id = other.id
            solution_id = solution.id

        from apps.api.main import app as full_app

        async def override_db():
            async with self.sessions() as db:
                yield db

        previous_override = full_app.dependency_overrides.get(get_db)
        full_app.dependency_overrides[get_db] = override_db
        client = AsyncClient(transport=ASGITransport(app=full_app), base_url="http://testserver")
        client.cookies.update(self.client.cookies)
        try:
            profile = await client.get(f"/api/company/profile?tenant_id={other_id}")
            integrations = await client.get(f"/api/integrations?tenant_id={other_id}")
            activity = await client.get(f"/api/company/events?tenant_id={other_id}")
            foreign_solution = await client.get(f"/api/solutions/{solution_id}")
            foreign_jobs = await client.get(f"/api/solutions/{solution_id}/jobs")
        finally:
            await client.aclose()
            if previous_override is None:
                full_app.dependency_overrides.pop(get_db, None)
            else:
                full_app.dependency_overrides[get_db] = previous_override

        self.assertEqual(profile.status_code, 200, profile.text)
        self.assertIn("tenant-a-profile", profile.text)
        combined = profile.text + integrations.text + activity.text
        self.assertNotIn("tenant-b-secret", combined)
        self.assertNotIn("tenant-b-activity", combined)
        self.assertEqual(foreign_solution.status_code, 404)
        self.assertEqual(foreign_jobs.status_code, 404)

    async def test_production_cookie_attributes(self):
        from apps.api.auth_cookies import set_session_cookies
        from fastapi import Response

        with patch.dict(
            os.environ,
            {"OPERLY_ENV": "production", "PUBLIC_BASE_URL": "https://operly.example"},
        ):
            response = Response()
            set_session_cookies(response, "session-secret", "csrf-secret")
        headers = "\n".join(response.headers.getlist("set-cookie"))
        self.assertIn("__Host-operly_session=", headers)
        self.assertIn("HttpOnly", headers)
        self.assertIn("Secure", headers)
        self.assertIn("SameSite=strict", headers)
        self.assertIn("Path=/", headers)
        self.assertNotIn("Domain=", headers)

    async def test_email_templates_have_text_alternatives_no_trackers_and_large_codes(self):
        signup = await self.signup()
        self.assertEqual(signup.status_code, 201)
        message = self.message("Verify")
        self.assertIn("font-size:34px", message.html_body)
        self.assertIn("Verify email", message.html_body)
        self.assertIn("expire in 30 minutes", message.text_body)
        self.assertNotIn("<img", message.html_body.lower())
        self.assertNotIn("utm_", message.html_body.lower())
        self.assertTrue(message.text_body.strip())

    async def test_email_links_ignore_untrusted_host_header(self):
        hostile = await self.new_client("http://attacker.invalid")
        try:
            response = await self.signup("host-safe@example.com", client=hostile)
            self.assertEqual(response.status_code, 201, response.text)
            message = self.message("Verify")
            self.assertIn("http://testserver/verify-email", message.text_body)
            self.assertNotIn("attacker.invalid", message.text_body)
        finally:
            await hostile.aclose()
