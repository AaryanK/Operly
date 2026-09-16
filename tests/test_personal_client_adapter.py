from __future__ import annotations

from datetime import datetime, timedelta
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.dependencies import AccountAuthContext, get_account_auth_context, get_db
from packages.database.db import Base
from packages.database.kernel_models import KernelRequestClaim
from packages.database.models import AppUser, AuthSession
from packages.database.schema import import_all_models
from packages.personal_modules.router import router as personal_tools_router


class PersonalClientAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.sessions() as db:
            self.alpha = AppUser(
                id="user-alpha",
                email="alpha@example.test",
                display_name="Alpha",
            )
            self.beta = AppUser(
                id="user-beta",
                email="beta@example.test",
                display_name="Beta",
            )
            db.add_all([self.alpha, self.beta])
            await db.commit()

        self.alpha_auth = self._auth(self.alpha, "session-alpha")
        self.beta_auth = self._auth(self.beta, "session-beta")
        self.auth = self.alpha_auth

        self.app = FastAPI()
        self.app.include_router(personal_tools_router)

        async def override_db():
            async with self.sessions() as db:
                yield db

        async def override_auth():
            return self.auth

        self.app.dependency_overrides[get_db] = override_db
        self.app.dependency_overrides[get_account_auth_context] = override_auth

    def _auth(self, user: AppUser, session_id: str) -> AccountAuthContext:
        return AccountAuthContext(
            user=user,
            session=AuthSession(
                id=session_id,
                token_hash=("0" if user.id == "user-alpha" else "2") * 64,
                csrf_token_hash=("1" if user.id == "user-alpha" else "3") * 64,
                user_id=user.id,
                expires_at=datetime.utcnow() + timedelta(hours=1),
            ),
        )

    async def asyncTearDown(self):
        self.app.dependency_overrides.clear()
        await self.engine.dispose()

    async def _post(self, payload: dict):
        transport = ASGITransport(app=self.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/personal-tools/client/submit", json=payload)

    @staticmethod
    async def _fake_run(*args, **kwargs):
        del args
        conversation = kwargs["conversation"]
        return {
            "message": "Fixture-backed Personal result.",
            "run_id": "runtime-fixture-run",
            "dispatch": "direct_capability",
            "objective_kind": "retrieve",
            "cycles": 1,
            "capability_calls": ["google.calendar.freebusy"],
            "approval_id": None,
            "error_code": None,
            "conversation_id": conversation.id,
            "artifacts": [],
        }

    async def test_completed_retry_replays_without_duplicate_runtime_submission(self):
        runner = AsyncMock(side_effect=self._fake_run)
        payload = {
            "message": "Find an afternoon next week when I am free.",
            "request_id": "client-request-001",
        }
        with patch("packages.personal_modules.client_adapter._run", runner):
            first = await self._post(payload)
            second = await self._post(payload)

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertFalse(first.json()["replayed"])
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(
            second.json()["conversation_id"],
            first.json()["conversation_id"],
        )
        self.assertEqual(runner.await_count, 1)

        async with self.sessions() as db:
            claims = (
                await db.scalars(select(KernelRequestClaim))
            ).all()
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].owner_user_id, self.alpha.id)
        self.assertEqual(claims[0].status, "completed")
        self.assertEqual(claims[0].request_id, "dragonzpyder:client-request-001")

    async def test_same_request_id_with_different_payload_is_rejected(self):
        runner = AsyncMock(side_effect=self._fake_run)
        with patch("packages.personal_modules.client_adapter._run", runner):
            first = await self._post(
                {
                    "message": "Check my calendar.",
                    "request_id": "client-request-002",
                }
            )
            conflict = await self._post(
                {
                    "message": "Draft a different email.",
                    "request_id": "client-request-002",
                }
            )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(
            conflict.json()["detail"]["code"],
            "PERSONAL_REQUEST_ID_CONFLICT",
        )
        self.assertEqual(runner.await_count, 1)

    async def test_workspace_authority_fields_are_not_part_of_client_contract(self):
        response = await self._post(
            {
                "message": "Check my calendar.",
                "request_id": "client-request-003",
                "selected_workspace_id": "workspace-attacker-controlled",
            }
        )
        self.assertEqual(response.status_code, 422, response.text)

    async def test_other_account_cannot_continue_first_accounts_conversation(self):
        runner = AsyncMock(side_effect=self._fake_run)
        with patch("packages.personal_modules.client_adapter._run", runner):
            alpha = await self._post(
                {
                    "message": "Check my calendar.",
                    "request_id": "client-request-alpha",
                }
            )
            self.assertEqual(alpha.status_code, 200, alpha.text)
            alpha_conversation_id = alpha.json()["conversation_id"]

            self.auth = self.beta_auth
            beta = await self._post(
                {
                    "message": "Continue that task.",
                    "request_id": "client-request-beta",
                    "conversation_id": alpha_conversation_id,
                }
            )

        self.assertEqual(beta.status_code, 404, beta.text)
        self.assertEqual(beta.json()["detail"], "Conversation not found")
        self.assertEqual(runner.await_count, 1)


if __name__ == "__main__":
    unittest.main()
