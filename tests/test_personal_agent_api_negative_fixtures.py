from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.agent_runtime_router import personal_router
from apps.api.dependencies import AccountAuthContext, get_account_auth_context, get_db
from packages.agent_runtime.interactive import Runtime1Agent, Runtime1Limits
from packages.agent_runtime.outcome_evaluation import (
    EffectLedger,
    OutcomeClaim,
    OutcomeOracle,
    load_fixtures,
)
from packages.agent_runtime.runtime import AgentRuntimeSettings
from packages.database.account_connector_models import AccountConnector
from packages.database.db import Base
from packages.database.models import AppUser, AuthSession
from packages.database.schema import import_all_models
from packages.personal_modules.google_provider import CALENDAR_FREEBUSY


FIXTURE_PATH = Path("evals/personal_task_completion/fixtures.json")


def fixture(fixture_id: str):
    return next(item for item in load_fixtures(FIXTURE_PATH) if item.fixture_id == fixture_id)


class PersonalAvailabilityModel:
    """Scripted model used only to exercise the mounted Personal API deterministically."""

    def __init__(self, *, connector_id: str | None = None) -> None:
        self.connector_id = connector_id
        self.offered_capabilities: list[str] = []
        self.respond_observations: list[list[dict]] = []

    async def interpret(self, request):
        del request
        return {
            "objective": "Check personal calendar availability next week in the afternoon",
            "kind": "retrieve",
            "operations": ["retrieve"],
            "resource_hints": ["calendar availability"],
            "requires_external_state": True,
            "requires_mutation": False,
            "requires_future_wait": False,
            "complexity": "simple",
        }

    async def decide(self, **kwargs):
        self.offered_capabilities = [item["id"] for item in kwargs["capabilities"]]
        arguments = {
            "time_min": "2026-09-21T12:00:00-05:00",
            "time_max": "2026-09-25T17:00:00-05:00",
            "calendar_ids": ["primary"],
            "time_zone": "America/Chicago",
        }
        if self.connector_id:
            arguments["connector_id"] = self.connector_id
        return {
            "move": "call",
            "capability_id": "google.calendar.freebusy",
            "arguments": arguments,
        }

    async def respond(self, **kwargs):
        observations = [dict(item) for item in kwargs.get("observations") or []]
        self.respond_observations.append(observations)
        codes = {str(item.get("error_code") or "") for item in observations}
        if "runtime_unavailable" in codes:
            return "I cannot access that requested Google account from this Personal scope."
        if "no_authorized_capabilities" in codes:
            return "I cannot check the calendar until an authorized Personal Google connector is available."
        return "Calendar availability checked."


class PersonalAgentApiNegativeFixtureTests(unittest.IsolatedAsyncioTestCase):
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

        self.auth = AccountAuthContext(
            user=self.alpha,
            session=AuthSession(
                id="session-alpha",
                token_hash="0" * 64,
                csrf_token_hash="1" * 64,
                user_id=self.alpha.id,
                expires_at=datetime.utcnow() + timedelta(hours=1),
            ),
        )
        self.app = FastAPI()
        self.app.include_router(personal_router)

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

    async def _post(self, *, model: PersonalAvailabilityModel, prompt: str):
        agent = Runtime1Agent(
            model=model,
            settings=AgentRuntimeSettings(enabled=True),
            limits=Runtime1Limits(max_capabilities=20),
        )
        transport = ASGITransport(app=self.app)
        with patch("apps.api.agent_runtime_router._agent", return_value=agent):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/api/personal-agent/chat",
                    json={"message": prompt},
                )

    async def test_wrong_account_connector_is_refused_before_credentials_or_network(self):
        selected = fixture("personal-wrong-account-access-v1")
        async with self.sessions() as db:
            db.add_all(
                [
                    AccountConnector(
                        id="connector-alpha",
                        user_id=self.alpha.id,
                        connector_type="google",
                        provider="google",
                        display_name="Alpha Google",
                        status="connected",
                        enabled=True,
                        provider_account_id="alpha-google@example.test",
                        granted_scopes_json=json.dumps([CALENDAR_FREEBUSY]),
                    ),
                    AccountConnector(
                        id="connector-beta",
                        user_id=self.beta.id,
                        connector_type="google",
                        provider="google",
                        display_name="Beta Google",
                        status="connected",
                        enabled=True,
                        provider_account_id="beta-google@example.test",
                        granted_scopes_json=json.dumps([CALENDAR_FREEBUSY]),
                    ),
                ]
            )
            await db.commit()

        model = PersonalAvailabilityModel(connector_id="connector-beta")
        token = AsyncMock(side_effect=AssertionError("credential boundary must not be crossed"))
        network = AsyncMock(side_effect=AssertionError("network boundary must not be crossed"))
        with patch("packages.personal_modules.google_provider.access_token", token), patch(
            "packages.personal_modules.google_provider.request_json", network
        ):
            response = await self._post(model=model, prompt=selected.prompt)

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["capability_calls"], ["google.calendar.freebusy"])
        self.assertIn("cannot access", payload["message"].lower())
        self.assertIn("google.calendar.freebusy", model.offered_capabilities)
        self.assertEqual(model.respond_observations[-1][0]["error_code"], "runtime_unavailable")
        token.assert_not_awaited()
        network.assert_not_awaited()

        verdict = OutcomeOracle().evaluate(
            fixture=selected,
            ledger=EffectLedger(),
            claim=OutcomeClaim(status="refused"),
        )
        self.assertTrue(verdict.passed, verdict.evidence)
        self.assertEqual(verdict.evidence, ("correct_refusal",))

    async def test_missing_connector_is_blocked_without_provider_execution(self):
        selected = fixture("personal-missing-connector-v1")
        model = PersonalAvailabilityModel()
        token = AsyncMock(side_effect=AssertionError("credential boundary must not be crossed"))
        network = AsyncMock(side_effect=AssertionError("network boundary must not be crossed"))
        with patch("packages.personal_modules.google_provider.access_token", token), patch(
            "packages.personal_modules.google_provider.request_json", network
        ):
            response = await self._post(model=model, prompt=selected.prompt)

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["capability_calls"], [])
        self.assertEqual(payload["error_code"], "no_authorized_capabilities")
        self.assertIn("connector", payload["message"].lower())
        self.assertEqual(
            model.respond_observations[-1][0]["error_code"],
            "no_authorized_capabilities",
        )
        token.assert_not_awaited()
        network.assert_not_awaited()

        verdict = OutcomeOracle().evaluate(
            fixture=selected,
            ledger=EffectLedger(),
            claim=OutcomeClaim(
                status="blocked",
                blocker_code=payload["error_code"],
            ),
        )
        self.assertEqual(verdict.completion_status, "incomplete")
        self.assertNotEqual(verdict.safety_status, "failed")
        self.assertEqual(verdict.evidence, ("expected_blocker_observed",))


if __name__ == "__main__":
    unittest.main()
