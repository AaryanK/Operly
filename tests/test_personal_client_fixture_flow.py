from __future__ import annotations

from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.dependencies import AccountAuthContext, get_account_auth_context, get_db
from packages.agent_runtime.interactive import Runtime1Agent, Runtime1Limits
from packages.agent_runtime.outcome_evaluation import EffectLedger
from packages.agent_runtime.runtime import AgentRuntimeSettings
from packages.database.db import Base
from packages.database.models import AppUser, AuthSession
from packages.database.schema import import_all_models
from packages.personal_modules.router import router as personal_tools_router
from packages.personal_modules.runtime import build_personal_runtime
from tests.test_personal_p3_calendar_draft import (
    EXPECTED_DRAFT_BODY,
    P3FixtureGoogleProvider,
    P3ScriptedModel,
)


class PersonalClientFixtureFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.sessions() as db:
            self.user = AppUser(
                id="user-alpha",
                email="alpha@example.test",
                display_name="Alpha",
            )
            db.add(self.user)
            await db.commit()

        self.auth = AccountAuthContext(
            user=self.user,
            session=AuthSession(
                id="session-alpha",
                token_hash="4" * 64,
                csrf_token_hash="5" * 64,
                user_id=self.user.id,
                expires_at=datetime.utcnow() + timedelta(hours=1),
            ),
        )
        self.app = FastAPI()
        self.app.include_router(personal_tools_router)

        async def override_db():
            async with self.sessions() as db:
                yield db

        async def override_auth():
            return self.auth

        self.app.dependency_overrides[get_db] = override_db
        self.app.dependency_overrides[get_account_auth_context] = override_auth

    async def asyncTearDown(self):
        self.app.dependency_overrides.clear()
        await self.engine.dispose()

    async def _post(self, client, *, model, provider, message, request_id, conversation_id=None):
        agent = Runtime1Agent(
            model=model,
            settings=AgentRuntimeSettings(enabled=True),
            limits=Runtime1Limits(max_capabilities=12, max_cycles=10, max_discoveries=4),
        )
        runtime = build_personal_runtime(personal_google_provider=provider)
        with patch("apps.api.agent_runtime_router._agent", return_value=agent), patch(
            "apps.api.agent_runtime_router.build_personal_runtime", return_value=runtime
        ):
            payload = {"message": message, "request_id": request_id}
            if conversation_id:
                payload["conversation_id"] = conversation_id
            return await client.post(
                "/api/personal-tools/client/submit",
                json=payload,
            )

    async def test_retry_safe_calendar_then_verified_draft_flow(self):
        ledger = EffectLedger()
        provider = P3FixtureGoogleProvider(ledger=ledger)
        model = P3ScriptedModel()
        transport = ASGITransport(app=self.app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            calendar = await self._post(
                client,
                model=model,
                provider=provider,
                message="Find a free afternoon next week.",
                request_id="dragon-calendar-001",
            )
            self.assertEqual(calendar.status_code, 200, calendar.text)
            calendar_payload = calendar.json()
            self.assertFalse(calendar_payload["replayed"])
            self.assertIn("Tuesday, September 22", calendar_payload["message"])
            self.assertEqual(
                calendar_payload["capability_calls"],
                ["google.calendar.list_calendars", "google.calendar.freebusy"],
            )

            calls_after_first = list(provider.calls)
            replay = await self._post(
                client,
                model=model,
                provider=provider,
                message="Find a free afternoon next week.",
                request_id="dragon-calendar-001",
            )
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertTrue(replay.json()["replayed"])
            self.assertEqual(
                replay.json()["conversation_id"],
                calendar_payload["conversation_id"],
            )
            self.assertEqual(provider.calls, calls_after_first)

            draft = await self._post(
                client,
                model=model,
                provider=provider,
                conversation_id=calendar_payload["conversation_id"],
                message="Draft an email to Alex asking about that time. Do not send it.",
                request_id="dragon-draft-001",
            )
            self.assertEqual(draft.status_code, 200, draft.text)
            draft_payload = draft.json()
            self.assertFalse(draft_payload["replayed"])
            self.assertEqual(
                draft_payload["message"],
                "Draft saved and read back successfully for Alex. I did not send it.",
            )
            self.assertEqual(
                draft_payload["capability_calls"],
                [
                    "google.people.search_contacts",
                    "google.gmail.create_draft",
                    "google.gmail.read_draft",
                ],
            )

        self.assertNotIn(
            "google.gmail.send_email",
            [capability_id for capability_id, _ in provider.calls],
        )
        self.assertEqual(len(ledger.by_type("calendar.freebusy.read")), 1)
        self.assertEqual(len(ledger.by_type("gmail.draft.created")), 1)
        self.assertEqual(len(ledger.by_type("gmail.draft.read")), 1)
        saved = provider.drafts["draft-p3-1"]
        self.assertEqual(saved["to"], ["alex@example.test"])
        self.assertEqual(saved["subject"], "Tuesday at 2?")
        self.assertEqual(saved["text_body"], EXPECTED_DRAFT_BODY)


if __name__ == "__main__":
    unittest.main()
