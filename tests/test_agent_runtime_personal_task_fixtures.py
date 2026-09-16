from __future__ import annotations

from pathlib import Path
import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.agent_runtime.interactive import Runtime1Agent, Runtime1Limits
from packages.agent_runtime.outcome_evaluation import (
    EffectLedger,
    EffectRecord,
    OutcomeClaim,
    OutcomeOracle,
    load_fixtures,
)
from packages.agent_runtime.runtime import AgentRuntimeSettings
from packages.database.db import Base
from packages.database.schema import import_all_models
from packages.kernel.contracts import CapabilityExecutionResult
from packages.personal_modules.runtime import build_personal_runtime
from packages.security.execution_context import (
    ExecutionContext,
    PERSONAL_EXECUTION_PERMISSIONS,
    ScopeKind,
)
from packages.security.surfaces import SurfaceKind


FIXTURE_PATH = Path("evals/personal_task_completion/fixtures.json")


def fixture(fixture_id: str):
    return next(item for item in load_fixtures(FIXTURE_PATH) if item.fixture_id == fixture_id)


def personal_context(user_id: str = "user-alpha") -> ExecutionContext:
    return ExecutionContext(
        workspace_id=None,
        user_id=user_id,
        membership_id=None,
        role="personal_owner",
        permissions=PERSONAL_EXECUTION_PERMISSIONS,
        channel="web",
        surface=SurfaceKind.PERSONAL_PRIVATE,
        conversation_id=f"conversation-{user_id}",
        scope_kind=ScopeKind.PERSONAL,
        principal_id=f"user:{user_id}",
        workspace_mode="personal",
    )


class ScriptedCallModel:
    def __init__(self, *, interpretation: dict, capability_id: str, arguments: dict) -> None:
        self.interpretation = interpretation
        self.capability_id = capability_id
        self.arguments = arguments
        self.decide_calls = 0

    async def interpret(self, request):
        del request
        return self.interpretation

    async def decide(self, **kwargs):
        self.decide_calls += 1
        offered = {item["id"] for item in kwargs["capabilities"]}
        if self.capability_id not in offered:
            return {"move": "finish", "message": "Required fixture capability was not offered."}
        return {
            "move": "call",
            "capability_id": self.capability_id,
            "arguments": dict(self.arguments),
        }

    async def respond(self, **kwargs):
        del kwargs
        return "Fixture operation completed."


class LedgerGoogleProvider:
    def __init__(self, *, ledger: EffectLedger, enabled: set[str]) -> None:
        self.ledger = ledger
        self.enabled = set(enabled)
        self.calls: list[tuple[str, dict]] = []

    async def is_available(self, db, *, context, capability):
        del db, context
        return capability.id in self.enabled

    async def execute(self, db, *, context, capability, arguments, minimum_context):
        del db, minimum_context
        self.calls.append((capability.id, dict(arguments)))
        if capability.id == "google.calendar.freebusy":
            external_id = f"freebusy-{len(self.calls)}"
            calendar_id = str(arguments["calendar_ids"][0])
            self.ledger.append(
                EffectRecord(
                    effect_type="calendar.freebusy.read",
                    owner_id=str(context.user_id),
                    external_id=external_id,
                    payload={
                        "calendar_id": calendar_id,
                        "time_zone": str(arguments.get("time_zone") or "UTC"),
                    },
                )
            )
            return CapabilityExecutionResult(
                value={
                    "timeMin": arguments["time_min"],
                    "timeMax": arguments["time_max"],
                    "calendars": {calendar_id: {"busy": []}},
                },
                resource_type="calendar_freebusy",
                resource_id="fixture-google",
            )
        if capability.id == "google.gmail.create_draft":
            external_id = f"draft-{len(self.calls)}"
            self.ledger.append(
                EffectRecord(
                    effect_type="gmail.draft.created",
                    owner_id=str(context.user_id),
                    external_id=external_id,
                    payload={
                        "to": list(arguments["to"]),
                        "subject": str(arguments.get("subject") or ""),
                        "text_body": str(arguments.get("text_body") or ""),
                    },
                )
            )
            return CapabilityExecutionResult(
                value={"draft_id": external_id, "message_id": f"message-{len(self.calls)}"},
                resource_type="gmail_draft",
                resource_id=external_id,
            )
        raise LookupError(f"fixture provider does not implement {capability.id}")


class PersonalTaskFixtureRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _run(self, *, fixture_id: str, capability_id: str, arguments: dict, interpretation: dict):
        selected = fixture(fixture_id)
        ledger = EffectLedger()
        provider = LedgerGoogleProvider(ledger=ledger, enabled={capability_id})
        runtime = build_personal_runtime(personal_google_provider=provider)
        model = ScriptedCallModel(
            interpretation=interpretation,
            capability_id=capability_id,
            arguments=arguments,
        )
        agent = Runtime1Agent(
            model=model,
            settings=AgentRuntimeSettings(enabled=True),
            limits=Runtime1Limits(max_capabilities=20),
        )
        async with self.sessions() as db:
            result = await agent.run(
                db,
                context=personal_context(selected.owner_id),
                message=selected.prompt,
                kernel=runtime,
                run_id=f"fixture-{selected.fixture_id}",
            )
        verdict = OutcomeOracle().evaluate(
            fixture=selected,
            ledger=ledger,
            claim=OutcomeClaim(status="success"),
        )
        return result, verdict, provider, ledger

    async def test_freebusy_fixture_runs_through_runtime1_and_kernel(self):
        result, verdict, provider, _ = await self._run(
            fixture_id="personal-freebusy-afternoon-v1",
            capability_id="google.calendar.freebusy",
            arguments={
                "time_min": "2026-09-21T12:00:00-05:00",
                "time_max": "2026-09-25T17:00:00-05:00",
                "calendar_ids": ["primary"],
                "time_zone": "America/Chicago",
            },
            interpretation={
                "objective": "Check calendar availability next week in the afternoon",
                "kind": "retrieve",
                "operations": ["retrieve"],
                "resource_hints": ["calendar availability"],
                "requires_external_state": True,
                "requires_mutation": False,
                "requires_future_wait": False,
                "complexity": "simple",
            },
        )
        self.assertTrue(verdict.passed, verdict.evidence)
        self.assertEqual(result.capability_calls, ("google.calendar.freebusy",))
        self.assertEqual(provider.calls[0][0], "google.calendar.freebusy")

    async def test_professor_draft_fixture_creates_draft_but_never_sends(self):
        result, verdict, provider, ledger = await self._run(
            fixture_id="personal-professor-email-draft-v1",
            capability_id="google.gmail.create_draft",
            arguments={
                "to": ["professor@example.test"],
                "subject": "Class absence",
                "text_body": "Professor,\n\nI was unable to attend class because of a family emergency.\n\nThank you.",
            },
            interpretation={
                "objective": "Draft a mail message to my professor explaining my absence",
                "kind": "act",
                "operations": ["act"],
                "resource_hints": ["mail message"],
                "requires_external_state": True,
                "requires_mutation": True,
                "requires_future_wait": False,
                "complexity": "simple",
            },
        )
        self.assertTrue(verdict.passed, verdict.evidence)
        self.assertEqual(result.capability_calls, ("google.gmail.create_draft",))
        self.assertEqual([call[0] for call in provider.calls], ["google.gmail.create_draft"])
        self.assertEqual(ledger.by_type("gmail.message.sent"), ())

    async def test_alex_invitation_variant_is_draft_only_and_exact(self):
        result, verdict, provider, ledger = await self._run(
            fixture_id="personal-invitation-draft-v1",
            capability_id="google.gmail.create_draft",
            arguments={
                "to": ["alex@example.test"],
                "subject": "Tuesday at 2?",
                "text_body": "Hi Alex,\n\nWould Tuesday at 2:00 PM work for you?",
            },
            interpretation={
                "objective": "Draft a mail message to Alex asking whether Tuesday at 2 works without sending it",
                "kind": "act",
                "operations": ["act"],
                "resource_hints": ["mail message"],
                "requires_external_state": True,
                "requires_mutation": True,
                "requires_future_wait": False,
                "complexity": "simple",
            },
        )
        self.assertTrue(verdict.passed, verdict.evidence)
        self.assertEqual(result.capability_calls, ("google.gmail.create_draft",))
        self.assertEqual([call[0] for call in provider.calls], ["google.gmail.create_draft"])
        self.assertEqual(ledger.by_type("gmail.message.sent"), ())


if __name__ == "__main__":
    unittest.main()
