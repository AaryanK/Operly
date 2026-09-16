from __future__ import annotations

import json
import re
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.agent_runtime.contracts import (
    AgentBudget,
    AgentPlan,
    AgentPlanStep,
    AgentRunStatus,
    AgentStepResult,
    AgentStepStatus,
    stable_step_request_id,
)
from packages.agent_runtime.store import create_run, transition_run
from packages.agent_runtime.worker import _personal_post_step_gate
from packages.database.account_connector_models import AccountConnector
from packages.database.agent_runtime_models import AgentRuntimeRun
from packages.database.db import Base
from packages.database.kernel_models import KernelApproval, KernelRequestClaim
from packages.database.models import AppUser
from packages.database.schema import import_all_models
from packages.kernel.approvals import decide_approval
from packages.kernel.contracts import RuntimeRequest
from packages.kernel.providers import ProviderExecutionUncertain
from packages.kernel.runtime import RuntimeExecutionError
from packages.personal_modules.google_provider import CALENDAR
from packages.personal_modules.reliable_google_provider import _stable_calendar_event_id
from packages.personal_modules.runtime import build_personal_runtime
from packages.security.execution_context import (
    ExecutionContext,
    PERSONAL_EXECUTION_PERMISSIONS,
    ScopeKind,
)
from packages.security.surfaces import SurfaceKind


EVENT_ARGS = {
    "connector_id": "google-alpha",
    "calendar_id": "primary",
    "summary": "Meeting with Alex",
    "start": "2026-09-22T14:00:00-05:00",
    "end": "2026-09-22T15:00:00-05:00",
    "attendees": ["alex@example.test"],
    "time_zone": "America/Chicago",
}


def verified_event(event_id: str, expected: dict) -> dict:
    return {
        "id": event_id,
        "status": "confirmed",
        "htmlLink": f"https://calendar.google.com/calendar/event?eid={event_id}",
        "summary": expected["summary"],
        "start": {
            "dateTime": expected["start"],
            "timeZone": expected["time_zone"],
        },
        "end": {
            "dateTime": expected["end"],
            "timeZone": expected["time_zone"],
        },
        "attendees": [{"email": email} for email in expected.get("attendees") or []],
    }


def invitation_plan(run_id: str) -> AgentPlan:
    return AgentPlan(
        run_id=run_id,
        goal="Invite Alex and book only after Alex confirms",
        budget=AgentBudget(max_steps=4, max_mutations=2),
        steps=(
            AgentPlanStep(
                step_id="send",
                capability_id="google.gmail.send_email",
                arguments={
                    "connector_id": "google-alpha",
                    "to": ["alex@example.test"],
                    "subject": "Tuesday at 2?",
                    "text_body": "Would Tuesday at 2 PM work?",
                },
            ),
            AgentPlanStep(
                step_id="recheck",
                capability_id="google.calendar.freebusy",
                arguments={
                    "connector_id": "google-alpha",
                    "time_min": "2026-09-22T13:00:00-05:00",
                    "time_max": "2026-09-22T16:00:00-05:00",
                    "calendar_ids": ["primary"],
                    "time_zone": "America/Chicago",
                },
            ),
            AgentPlanStep(
                step_id="book",
                capability_id="google.calendar.create_event",
                arguments=dict(EVENT_ARGS),
            ),
        ),
    )


class PersonalVerifiedCalendarBookingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as db:
            db.add(AppUser(id="user-alpha", email="alpha@example.test", display_name="Alpha"))
            db.add(
                AccountConnector(
                    id="google-alpha",
                    user_id="user-alpha",
                    connector_type="google_account",
                    provider="google",
                    display_name="Personal Google",
                    status="connected",
                    enabled=True,
                    provider_account_id="alpha@example.test",
                    granted_scopes_json=json.dumps([CALENDAR]),
                )
            )
            await db.commit()
        self.runtime = build_personal_runtime()
        self.context = ExecutionContext(
            workspace_id=None,
            user_id="user-alpha",
            membership_id=None,
            role="personal_owner",
            permissions=frozenset(PERSONAL_EXECUTION_PERMISSIONS),
            channel="web",
            surface=SurfaceKind.PERSONAL_PRIVATE,
            conversation_id="conversation-alpha",
            scope_kind=ScopeKind.PERSONAL,
            principal_id="user:user-alpha",
            workspace_mode="personal",
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    def _request(self, request_id: str, *, approval_id: str | None = None) -> RuntimeRequest:
        return RuntimeRequest(
            goal="Create the reviewed meeting with Alex after verified acceptance",
            capability_id="google.calendar.create_event",
            arguments=dict(EVENT_ARGS),
            conversation_id="conversation-alpha",
            request_id=request_id,
            approval_id=approval_id,
        )

    async def _pending_approval(self, request_id: str) -> KernelApproval:
        async with self.sessions() as db:
            with self.assertRaises(RuntimeExecutionError) as caught:
                await self.runtime.execute(
                    db,
                    context=self.context,
                    request=self._request(request_id),
                )
            self.assertEqual(caught.exception.code, "approval_required")
            row = await db.get(KernelApproval, caught.exception.approval_id)
            self.assertIsNotNone(row)
            return row

    async def _approve(self, approval_id: str) -> None:
        async with self.sessions() as db:
            await decide_approval(
                db,
                context=self.context,
                approval_id=approval_id,
                approved=True,
                decided_by_user_id="user-alpha",
            )
            await db.commit()

    async def test_stable_event_identity_uses_google_compatible_alphabet(self):
        value = _stable_calendar_event_id(
            owner_user_id="user-alpha",
            request_id="calendar-stable-1",
        )
        self.assertRegex(value, re.compile(r"^[0-9a-f]{52}$"))
        self.assertEqual(
            value,
            _stable_calendar_event_id(
                owner_user_id="user-alpha",
                request_id="calendar-stable-1",
            ),
        )

    async def test_success_persists_intent_then_reads_back_exact_event_and_replay_does_not_recreate(self):
        request_id = "calendar-success-1"
        approval = await self._pending_approval(request_id)
        await self._approve(approval.id)
        expected_event_id = _stable_calendar_event_id(
            owner_user_id="user-alpha",
            request_id=request_id,
        )
        observed: dict = {}

        async def create_once(token: str, *, calendar_id: str, payload: dict, params=None):
            self.assertEqual(token, "fixture-token")
            self.assertEqual(calendar_id, "primary")
            self.assertEqual(payload["id"], expected_event_id)
            self.assertIsNone(params)
            async with self.sessions() as inspect_db:
                claim = await inspect_db.scalar(
                    select(KernelRequestClaim).where(
                        KernelRequestClaim.request_id == request_id
                    )
                )
                observed["status"] = claim.status
                observed.update(json.loads(claim.response_json))
            return {"id": expected_event_id}

        async def verify(token: str, *, calendar_id: str, event_id: str, expected: dict):
            self.assertEqual(token, "fixture-token")
            self.assertEqual(calendar_id, "primary")
            self.assertEqual(event_id, expected_event_id)
            return verified_event(event_id, expected)

        create = AsyncMock(side_effect=create_once)
        readback = AsyncMock(side_effect=verify)
        with patch(
            "packages.personal_modules.reliable_google_provider.access_token",
            new=AsyncMock(return_value="fixture-token"),
        ), patch(
            "packages.personal_modules.reliable_google_provider._calendar_create_once",
            new=create,
        ), patch(
            "packages.personal_modules.reliable_google_provider._verify_calendar_event",
            new=readback,
        ):
            async with self.sessions() as db:
                result = await self.runtime.execute(
                    db,
                    context=self.context,
                    request=self._request(request_id, approval_id=approval.id),
                )
            async with self.sessions() as db:
                replay = await self.runtime.execute(
                    db,
                    context=self.context,
                    request=self._request(request_id, approval_id=approval.id),
                )

        self.assertEqual(observed["status"], "running")
        self.assertEqual(observed["intent"], "calendar_create")
        self.assertEqual(observed["approval_id"], approval.id)
        self.assertEqual(observed["event_id"], expected_event_id)
        self.assertEqual(observed["calendar_id"], "primary")
        self.assertEqual(result.result["event_id"], expected_event_id)
        self.assertEqual(result.result["verification_status"], "read_back")
        self.assertTrue(result.result["event_link"].startswith("https://calendar.google.com/"))
        self.assertEqual(replay.result["event_id"], expected_event_id)
        self.assertEqual(create.await_count, 1)
        self.assertEqual(readback.await_count, 1)

        async with self.sessions() as db:
            claim = await db.scalar(
                select(KernelRequestClaim).where(KernelRequestClaim.request_id == request_id)
            )
            stored_approval = await db.get(KernelApproval, approval.id)
        self.assertEqual(claim.status, "completed")
        self.assertEqual(stored_approval.status, "consumed")

    async def test_ambiguous_create_reconciles_by_stable_event_id_without_second_insert(self):
        request_id = "calendar-reconcile-1"
        approval = await self._pending_approval(request_id)
        await self._approve(approval.id)
        create = AsyncMock(side_effect=ProviderExecutionUncertain("socket closed after insert"))

        async def verify(token: str, *, calendar_id: str, event_id: str, expected: dict):
            return verified_event(event_id, expected)

        readback = AsyncMock(side_effect=verify)
        with patch(
            "packages.personal_modules.reliable_google_provider.access_token",
            new=AsyncMock(return_value="fixture-token"),
        ), patch(
            "packages.personal_modules.reliable_google_provider._calendar_create_once",
            new=create,
        ), patch(
            "packages.personal_modules.reliable_google_provider._verify_calendar_event",
            new=readback,
        ):
            async with self.sessions() as db:
                result = await self.runtime.execute(
                    db,
                    context=self.context,
                    request=self._request(request_id, approval_id=approval.id),
                )

        self.assertEqual(result.result["provider_status"], "reconciled_created")
        self.assertEqual(result.result["verification_status"], "read_back")
        self.assertEqual(create.await_count, 1)
        self.assertEqual(readback.await_count, 1)

    async def test_unverified_create_is_durable_uncertain_and_blocks_blind_retry(self):
        request_id = "calendar-uncertain-1"
        approval = await self._pending_approval(request_id)
        await self._approve(approval.id)
        create = AsyncMock(return_value={"id": "provider-ack"})
        readback = AsyncMock(return_value=None)

        with patch(
            "packages.personal_modules.reliable_google_provider.access_token",
            new=AsyncMock(return_value="fixture-token"),
        ), patch(
            "packages.personal_modules.reliable_google_provider._calendar_create_once",
            new=create,
        ), patch(
            "packages.personal_modules.reliable_google_provider._verify_calendar_event",
            new=readback,
        ):
            async with self.sessions() as db:
                with self.assertRaises(RuntimeExecutionError) as uncertain:
                    await self.runtime.execute(
                        db,
                        context=self.context,
                        request=self._request(request_id, approval_id=approval.id),
                    )
            self.assertEqual(uncertain.exception.code, "execution_outcome_uncertain")
            self.assertEqual(
                uncertain.exception.details["recovery"],
                "read_calendar_event_by_stable_event_id_before_retry",
            )

            async with self.sessions() as db:
                with self.assertRaises(RuntimeExecutionError) as replay:
                    await self.runtime.execute(
                        db,
                        context=self.context,
                        request=self._request(request_id, approval_id=approval.id),
                    )

        self.assertEqual(replay.exception.code, "request_in_progress")
        self.assertEqual(create.await_count, 1)
        async with self.sessions() as db:
            claim = await db.scalar(
                select(KernelRequestClaim).where(KernelRequestClaim.request_id == request_id)
            )
        self.assertEqual(claim.status, "uncertain")
        metadata = json.loads(claim.response_json)
        self.assertEqual(metadata["intent"], "calendar_create")
        self.assertEqual(metadata["event_id"], _stable_calendar_event_id(
            owner_user_id="user-alpha",
            request_id=request_id,
        ))
        self.assertIn("uncertainty_reason", metadata)

    async def test_personal_lifecycle_terminal_result_retains_verified_event_link(self):
        run_id = "verified-booking-task"
        plan = invitation_plan(run_id)
        async with self.sessions() as db:
            row = await create_run(db, context=self.context, plan=plan)
            row.grants_reference_json = json.dumps(
                {
                    "authority_mode": "live_reresolve",
                    "objective_ir": {"requires_future_wait": True},
                }
            )
            await transition_run(db, run_id=run_id, to_status="running")
            row.wait_predicate_json = json.dumps(
                {
                    "kind": "gmail_thread_reply",
                    "state": "calendar_rechecked",
                    "calendar_event_step_id": "book",
                },
                sort_keys=True,
            )
            await db.commit()

        event_id = "abcdef0123456789abcdef0123456789abcdef0123456789abcd"
        event_link = f"https://calendar.google.com/calendar/event?eid={event_id}"
        result = AgentStepResult(
            step_id="book",
            capability_id="google.calendar.create_event",
            request_id=stable_step_request_id(run_id, "book"),
            status=AgentStepStatus.COMPLETED,
            result={
                "calendar_id": "primary",
                "event_id": event_id,
                "event_link": event_link,
                "verification_status": "read_back",
            },
        )
        async with self.sessions() as db:
            row = await db.get(AgentRuntimeRun, run_id)
            gated = await _personal_post_step_gate(
                db,
                row,
                plan,
                plan.steps[2],
                result,
                (result,),
            )
            persisted = await db.get(AgentRuntimeRun, run_id)
            outcome = json.loads(persisted.result_json)

        self.assertIsNotNone(gated)
        self.assertEqual(gated.status, AgentRunStatus.COMPLETED)
        self.assertEqual(persisted.status, "completed")
        self.assertEqual(outcome["calendar_event_id"], event_id)
        self.assertEqual(outcome["calendar_event_link"], event_link)
        self.assertEqual(outcome["verification_status"], "read_back")


if __name__ == "__main__":
    unittest.main()
