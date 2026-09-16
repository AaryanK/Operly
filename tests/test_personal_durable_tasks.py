from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.dependencies import AccountAuthContext, get_account_auth_context, get_db
from packages.agent_runtime.worker import PersonalAgentTaskWorker, _requires_future_wait
from packages.database.agent_runtime_models import AgentRuntimeRun
from packages.database.db import Base
from packages.database.models import AppUser, AuthSession
from packages.database.schema import import_all_models
from packages.kernel.contracts import CapabilityRisk, CapabilitySpec
from packages.personal_modules.router import router as personal_tools_router


def freebusy_capability() -> CapabilitySpec:
    return CapabilitySpec(
        id="google.calendar.freebusy",
        version="1",
        display_name="Calendar free/busy",
        description="Read Personal calendar free/busy windows",
        provider_id="personal.google",
        scopes=frozenset({"personal"}),
        input_schema={
            "type": "object",
            "properties": {
                "time_min": {"type": "string"},
                "time_max": {"type": "string"},
                "time_zone": {"type": "string"},
                "calendar_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["time_min", "time_max", "time_zone", "calendar_ids"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        risk=CapabilityRisk.READ_ONLY,
        approval_required=False,
    )


class FakePlanningRuntime:
    async def available_capabilities(self, db, *, context, query=None, limit=50):
        del db, context, query, limit
        return (freebusy_capability(),)


class FakePlannerModel:
    def __init__(self, *, requires_future_wait: bool = False) -> None:
        self.calls = 0
        self.interpret_calls = 0
        self.requires_future_wait = requires_future_wait

    async def interpret(self, request):
        del request
        self.interpret_calls += 1
        if self.requires_future_wait:
            return {
                "objective": "Wait for a recipient confirmation before creating a calendar event",
                "kind": "wait",
                "operations": ["retrieve", "act", "wait"],
                "resource_hints": ["mail message", "availability", "calendar event"],
                "requires_external_state": True,
                "requires_mutation": True,
                "requires_future_wait": True,
                "complexity": "compound",
            }
        return {
            "objective": "List calendar availability next week",
            "kind": "retrieve",
            "operations": ["retrieve"],
            "resource_hints": ["availability"],
            "requires_external_state": True,
            "requires_mutation": False,
            "requires_future_wait": False,
            "complexity": "simple",
        }

    async def plan(self, request):
        del request
        self.calls += 1
        return {
            "steps": [
                {
                    "capability_id": "google.calendar.freebusy",
                    "arguments": {
                        "time_min": "2026-09-21T12:00:00-05:00",
                        "time_max": "2026-09-25T17:00:00-05:00",
                        "time_zone": "America/Chicago",
                        "calendar_ids": ["primary"],
                    },
                }
            ]
        }


class PersonalDurableTaskTests(unittest.IsolatedAsyncioTestCase):
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
            self.other = AppUser(
                id="user-beta",
                email="beta@example.test",
                display_name="Beta",
            )
            db.add_all((self.user, self.other))
            await db.commit()

        self.auth = self._auth(self.user, "session-alpha")
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
                token_hash=("4" if user.id == "user-alpha" else "6") * 64,
                csrf_token_hash=("5" if user.id == "user-alpha" else "7") * 64,
                user_id=user.id,
                expires_at=datetime.utcnow() + timedelta(hours=1),
            ),
        )

    async def asyncTearDown(self):
        self.app.dependency_overrides.clear()
        await self.engine.dispose()

    async def test_task_submission_commits_before_202_and_retry_does_not_fork_conversation(self):
        model = FakePlannerModel()
        transport = ASGITransport(app=self.app)
        previous_enabled = os.environ.get("OPERLY_AGENT_RUNTIME_ENABLED")
        os.environ["OPERLY_AGENT_RUNTIME_ENABLED"] = "1"
        try:
            with patch(
                "packages.personal_modules.task_service.build_personal_runtime",
                return_value=FakePlanningRuntime(),
            ), patch(
                "packages.personal_modules.task_service.OpenAICompatibleAgentModel",
                return_value=model,
            ):
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    first = await client.post(
                        "/api/personal-tools/client/tasks",
                        json={
                            "message": "Find an afternoon next week when I am free.",
                            "request_id": "durable-calendar-001",
                        },
                    )
                    self.assertEqual(first.status_code, 202, first.text)
                    first_payload = first.json()
                    self.assertEqual(first_payload["status"], "queued")
                    self.assertFalse(first_payload["replayed"])
                    self.assertTrue(first_payload["conversation_id"])
                    self.assertEqual(len(first_payload["steps"]), 1)
                    self.assertEqual(model.interpret_calls, 1)
                    self.assertEqual(model.calls, 1)
                    objective_ir = first_payload["grants_reference"]["objective_ir"]
                    self.assertFalse(objective_ir["requires_future_wait"])

                    # Simulate a lost first response: retry only with the same request ID,
                    # without knowing the conversation ID returned above.
                    replay = await client.post(
                        "/api/personal-tools/client/tasks",
                        json={
                            "message": "Find an afternoon next week when I am free.",
                            "request_id": "durable-calendar-001",
                        },
                    )
                    self.assertEqual(replay.status_code, 202, replay.text)
                    replay_payload = replay.json()
                    self.assertTrue(replay_payload["replayed"])
                    self.assertEqual(replay_payload["task_id"], first_payload["task_id"])
                    self.assertEqual(
                        replay_payload["conversation_id"],
                        first_payload["conversation_id"],
                    )
                    self.assertEqual(model.interpret_calls, 1)
                    self.assertEqual(model.calls, 1)

            async with self.sessions() as db:
                persisted = await db.get(AgentRuntimeRun, first_payload["task_id"])
                self.assertIsNotNone(persisted)
                self.assertEqual(persisted.status, "queued")
                self.assertEqual(persisted.owner_user_id, "user-alpha")
                self.assertEqual(persisted.conversation_id, first_payload["conversation_id"])
                grants = json.loads(persisted.grants_reference_json)
                self.assertFalse(grants["objective_ir"]["requires_future_wait"])
        finally:
            if previous_enabled is None:
                os.environ.pop("OPERLY_AGENT_RUNTIME_ENABLED", None)
            else:
                os.environ["OPERLY_AGENT_RUNTIME_ENABLED"] = previous_enabled

    async def test_future_wait_semantic_is_persisted_and_controls_lifecycle_gate(self):
        model = FakePlannerModel(requires_future_wait=True)
        transport = ASGITransport(app=self.app)
        previous_enabled = os.environ.get("OPERLY_AGENT_RUNTIME_ENABLED")
        os.environ["OPERLY_AGENT_RUNTIME_ENABLED"] = "1"
        try:
            with patch(
                "packages.personal_modules.task_service.build_personal_runtime",
                return_value=FakePlanningRuntime(),
            ), patch(
                "packages.personal_modules.task_service.OpenAICompatibleAgentModel",
                return_value=model,
            ):
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    created = await client.post(
                        "/api/personal-tools/client/tasks",
                        json={
                            "message": "Wait for Alex to confirm before booking the meeting.",
                            "request_id": "durable-wait-001",
                        },
                    )
                    self.assertEqual(created.status_code, 202, created.text)
                    payload = created.json()
                    self.assertTrue(payload["grants_reference"]["objective_ir"]["requires_future_wait"])

            async with self.sessions() as db:
                row = await db.get(AgentRuntimeRun, payload["task_id"])
                self.assertTrue(_requires_future_wait(row))
                row.grants_reference_json = json.dumps(
                    {
                        "authority_mode": "live_reresolve",
                        "objective_ir": {"requires_future_wait": False},
                    }
                )
                await db.commit()
                self.assertFalse(_requires_future_wait(row))
        finally:
            if previous_enabled is None:
                os.environ.pop("OPERLY_AGENT_RUNTIME_ENABLED", None)
            else:
                os.environ["OPERLY_AGENT_RUNTIME_ENABLED"] = previous_enabled

    async def test_task_status_is_personal_owner_scoped_and_cancel_is_terminal(self):
        model = FakePlannerModel()
        transport = ASGITransport(app=self.app)
        previous_enabled = os.environ.get("OPERLY_AGENT_RUNTIME_ENABLED")
        os.environ["OPERLY_AGENT_RUNTIME_ENABLED"] = "1"
        try:
            with patch(
                "packages.personal_modules.task_service.build_personal_runtime",
                return_value=FakePlanningRuntime(),
            ), patch(
                "packages.personal_modules.task_service.OpenAICompatibleAgentModel",
                return_value=model,
            ):
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    created = await client.post(
                        "/api/personal-tools/client/tasks",
                        json={
                            "message": "Find an afternoon next week when I am free.",
                            "request_id": "durable-cancel-001",
                        },
                    )
                    task_id = created.json()["task_id"]

                    self.auth = self._auth(self.other, "session-beta")
                    hidden = await client.get(f"/api/personal-tools/client/tasks/{task_id}")
                    self.assertEqual(hidden.status_code, 404)

                    self.auth = self._auth(self.user, "session-alpha-2")
                    cancelled = await client.post(
                        f"/api/personal-tools/client/tasks/{task_id}/cancel"
                    )
                    self.assertEqual(cancelled.status_code, 200, cancelled.text)
                    self.assertEqual(cancelled.json()["status"], "cancelled")
                    self.assertTrue(cancelled.json()["cancellation_requested"])
        finally:
            if previous_enabled is None:
                os.environ.pop("OPERLY_AGENT_RUNTIME_ENABLED", None)
            else:
                os.environ["OPERLY_AGENT_RUNTIME_ENABLED"] = previous_enabled

    async def test_worker_candidate_selection_recovers_expired_lease_but_not_active_or_cancelled(self):
        now = datetime.utcnow()
        async with self.sessions() as db:
            base = dict(
                scope_kind="personal",
                workspace_id=None,
                owner_user_id="user-alpha",
                authority_user_id="user-alpha",
                principal_id="user:user-alpha",
                conversation_id=None,
                source_channel="dragonzpyder_cli",
                source_surface="personal_private",
                goal="fixture",
                plan_json='{"run_id":"x","goal":"fixture","steps":[]}',
                budget_json='{"max_steps":1,"max_mutations":0}',
            )
            db.add_all(
                (
                    AgentRuntimeRun(id="queued-task", status="queued", **base),
                    AgentRuntimeRun(
                        id="active-task",
                        status="running",
                        lease_token="worker-a",
                        lease_until=now + timedelta(minutes=2),
                        **base,
                    ),
                    AgentRuntimeRun(
                        id="expired-task",
                        status="running",
                        lease_token="worker-dead",
                        lease_until=now - timedelta(seconds=1),
                        **base,
                    ),
                    AgentRuntimeRun(
                        id="cancelled-task",
                        status="cancelled",
                        cancellation_requested=True,
                        **base,
                    ),
                )
            )
            await db.commit()

        worker = PersonalAgentTaskWorker(
            session_factory=self.sessions,
            concurrency=8,
            worker_id="worker-test",
        )
        candidates = set(await worker._candidate_ids())
        self.assertIn("queued-task", candidates)
        self.assertIn("expired-task", candidates)
        self.assertNotIn("active-task", candidates)
        self.assertNotIn("cancelled-task", candidates)


if __name__ == "__main__":
    unittest.main()
