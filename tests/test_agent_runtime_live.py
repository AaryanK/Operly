from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.agent_runtime.context import ContextItem, ContextKind
from packages.agent_runtime.inference import (
    AgentInferenceError,
    InferenceRoute,
    OpenAICompatibleAgentModel,
)
from packages.agent_runtime.interactive import Runtime1Agent
from packages.agent_runtime.runtime import AgentRuntimeSettings
from packages.agent_runtime.spend import (
    AgentSpendMeter,
    ModelPrice,
    PriceSnapshot,
    SpendLimits,
    SpendScope,
)
from packages.database.agent_spend_models import AgentSpendReservationRecord
from packages.database.db import Base
from packages.database.schema import import_all_models
from packages.personal_modules.runtime import build_personal_runtime
from packages.security.execution_context import (
    ExecutionContext,
    PERSONAL_EXECUTION_PERMISSIONS,
    ScopeKind,
)
from packages.security.surfaces import SurfaceKind
from packages.workspace_modules.integrations.discord.bot import _discordify


class FakeModel:
    def __init__(self, interpretation, response="answer") -> None:
        self.interpretation = interpretation
        self.response = response
        self.interpret_requests = []
        self.respond_calls = []
        self.decide_calls = []

    async def interpret(self, request):
        self.interpret_requests.append(request)
        return self.interpretation

    async def respond(self, **kwargs):
        self.respond_calls.append(kwargs)
        return self.response

    async def decide(self, **kwargs):
        self.decide_calls.append(kwargs)
        return {"move": "finish", "message": "done"}


class SpendBindingFakeModel(FakeModel):
    def __init__(self, interpretation, response="answer") -> None:
        super().__init__(interpretation, response=response)
        self.events: list[str] = []
        self.bound_context = None
        self.bound_run_id = None

    def bind_spend_context(self, *, context, run_id):
        self.events.append("bind")
        self.bound_context = context
        self.bound_run_id = run_id

    async def interpret(self, request):
        self.events.append("interpret")
        return await super().interpret(request)


class StubResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self.payload = payload or {}

    def json(self):
        return self.payload


class StubAsyncClient:
    def __init__(self, responses: list[StubResponse], calls: list[dict]) -> None:
        self.responses = responses
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, path, *, headers, json):
        self.calls.append({"path": path, "headers": dict(headers), "json": dict(json)})
        if not self.responses:
            raise AssertionError("unexpected extra provider dispatch")
        return self.responses.pop(0)


def personal_context(*, full_permissions: bool = False) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=None,
        user_id="user-1",
        membership_id=None,
        role="personal_owner",
        permissions=(
            PERSONAL_EXECUTION_PERMISSIONS
            if full_permissions
            else frozenset({"workspace:read"})
        ),
        channel="web",
        surface=SurfaceKind.PERSONAL_PRIVATE,
        conversation_id="conversation-1",
        scope_kind=ScopeKind.PERSONAL,
        principal_id="user:user-1",
        workspace_mode="personal",
    )


class Runtime1LiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_tool_request_never_discovers_or_executes_capabilities(self):
        model = FakeModel(
            {
                "objective": "Explain recursion",
                "kind": "respond",
                "operations": ["respond"],
                "resource_hints": [],
                "requires_external_state": False,
                "requires_mutation": False,
                "requires_future_wait": False,
                "complexity": "simple",
            },
            response="Recursion is when a function solves a problem using smaller instances of itself.",
        )
        agent = Runtime1Agent(
            model=model,
            settings=AgentRuntimeSettings(enabled=True),
        )
        relevant = ContextItem(
            key="relevant",
            kind=ContextKind.CONVERSATION,
            text="Earlier we were discussing recursion and base cases.",
            relevance=1.0,
        )
        irrelevant = ContextItem(
            key="irrelevant",
            kind=ContextKind.CONVERSATION,
            text="Unrelated restaurant booking details.",
        )

        result = await agent.run(
            None,
            context=personal_context(),
            message="Explain recursion again.",
            kernel=None,
            context_items=[relevant, irrelevant],
            run_id="run-no-tool",
        )

        self.assertEqual(result.dispatch, "respond")
        self.assertEqual(result.capability_calls, ())
        self.assertEqual(len(model.respond_calls), 1)
        supplied = model.respond_calls[0]["context_items"]
        self.assertEqual(len(supplied), 1)
        self.assertIn("recursion", supplied[0]["text"].lower())

    async def test_runtime_binds_trusted_spend_context_before_first_model_call(self):
        model = SpendBindingFakeModel(
            {
                "objective": "Explain recursion",
                "kind": "respond",
                "operations": ["respond"],
                "resource_hints": [],
                "requires_external_state": False,
                "requires_mutation": False,
                "requires_future_wait": False,
                "complexity": "simple",
            }
        )
        agent = Runtime1Agent(model=model, settings=AgentRuntimeSettings(enabled=True))
        context = personal_context()
        result = await agent.run(
            None,
            context=context,
            message="Explain recursion.",
            kernel=None,
            run_id="trusted-runtime-run",
        )
        self.assertEqual(result.dispatch, "respond")
        self.assertEqual(model.events[:2], ["bind", "interpret"])
        self.assertIs(model.bound_context, context)
        self.assertEqual(model.bound_run_id, "trusted-runtime-run")

    def test_email_retrieval_query_surfaces_read_tools_before_email_mutations(self):
        registry = build_personal_runtime().registry
        results = registry.search(
            "Search Dad's emails | resources emails | operations retrieve",
            context=personal_context(full_permissions=True),
            effective_only=True,
            limit=12,
        )
        ids = [spec.id for spec in results]

        self.assertIn("google.gmail.search", ids)
        self.assertIn("google.gmail.read_message", ids)
        self.assertLess(ids.index("google.gmail.search"), 4)
        self.assertLess(ids.index("google.gmail.read_message"), 6)
        if "google.gmail.send_email" in ids:
            self.assertLess(ids.index("google.gmail.search"), ids.index("google.gmail.send_email"))
            self.assertLess(ids.index("google.gmail.read_message"), ids.index("google.gmail.send_email"))

    def test_plain_email_wording_still_finds_gmail_search(self):
        registry = build_personal_runtime().registry
        ids = [
            spec.id
            for spec in registry.search(
                "search my emails for dad's emails",
                context=personal_context(full_permissions=True),
                effective_only=True,
                limit=12,
            )
        ]
        self.assertIn("google.gmail.search", ids)

    def test_inference_route_uses_fixed_provider_destinations(self):
        with patch.dict(
            os.environ,
            {
                "OPERLY_AGENT_MODEL_PROVIDER": "groq",
                "GROQ_API_KEY": "test-key",  # pragma: allowlist secret
                "OPERLY_AGENT_MODEL_BASE_URL": "https://attacker.invalid/v1",
                "OPERLY_AGENT_MODEL_ID": "openai/gpt-oss-120b",
            },
            clear=False,
        ):
            route = InferenceRoute.from_environment()
        self.assertEqual(route.provider, "groq")
        self.assertEqual(route.base_url, "https://api.groq.com/openai/v1")
        self.assertNotIn("attacker", route.base_url)

    def test_unknown_provider_fails_closed(self):
        with patch.dict(
            os.environ,
            {"OPERLY_AGENT_MODEL_PROVIDER": "user-controlled-provider"},
            clear=False,
        ):
            with self.assertRaises(AgentInferenceError) as caught:
                InferenceRoute.from_environment()
        self.assertEqual(caught.exception.code, "inference_not_configured")

    def test_next_move_rejects_authority_fields(self):
        model = FakeModel({})
        agent = Runtime1Agent(model=model, settings=AgentRuntimeSettings(enabled=True))
        with self.assertRaises(ValueError):
            agent._decode_decision(
                {
                    "move": "call",
                    "capability_id": "tasks.create",
                    "arguments": {"title": "x"},
                    "permissions": ["admin"],
                }
            )

    def test_next_move_allows_no_tool_finish(self):
        model = FakeModel({})
        agent = Runtime1Agent(model=model, settings=AgentRuntimeSettings(enabled=True))
        payload = agent._decode_decision({"move": "finish", "message": "Nothing else is needed."})
        self.assertEqual(payload["move"], "finish")

    def test_next_move_normalizes_harmless_model_metadata(self):
        model = FakeModel({})
        agent = Runtime1Agent(model=model, settings=AgentRuntimeSettings(enabled=True))
        payload = agent._decode_decision(
            {
                "move": "call",
                "capability_id": "tasks.create",
                "arguments": {"title": "Test Operly Runtime 1.0"},
                "rationale": "The user explicitly asked to create a task.",
                "confidence": 0.99,
            }
        )
        self.assertEqual(
            payload,
            {
                "move": "call",
                "capability_id": "tasks.create",
                "arguments": {"title": "Test Operly Runtime 1.0"},
            },
        )

    def test_next_move_accepts_single_json_fence_but_still_normalizes(self):
        model = FakeModel({})
        agent = Runtime1Agent(model=model, settings=AgentRuntimeSettings(enabled=True))
        payload = agent._decode_decision(
            "```json\n"
            '{"move":"discover","query":"personal task creation","note":"search first"}'
            "\n```"
        )
        self.assertEqual(payload, {"move": "discover", "query": "personal task creation"})

    async def test_user_facing_model_identity_is_operly_not_provider_identity(self):
        model = OpenAICompatibleAgentModel(
            route=InferenceRoute(
                provider="groq",
                base_url="https://api.groq.com/openai/v1",
                api_key=None,
                model_id="openai/gpt-oss-120b",
            )
        )
        chat = AsyncMock(return_value="I’m Operly.")
        model._chat = chat
        answer = await model.respond(
            objective="Tell the user who they are speaking with",
            user_message="Are you ChatGPT or Operly?",
        )
        self.assertEqual(answer, "I’m Operly.")
        system = chat.await_args.kwargs["system"]
        self.assertIn("You are Operly", system)
        self.assertIn("Always identify yourself as Operly", system)
        self.assertIn("never as ChatGPT", system)

    def test_discord_formatter_converts_markdown_tables_to_native_friendly_bullets(self):
        rendered = _discordify(
            "## Capabilities\n\n"
            "| Tool | Purpose |\n"
            "| --- | --- |\n"
            "| Tasks | Create work |\n"
            "| Calendar | Find meetings |\n"
        )
        self.assertIn("## Capabilities", rendered)
        self.assertIn("- **Tasks**", rendered)
        self.assertIn("**Purpose:** Create work", rendered)
        self.assertIn("- **Calendar**", rendered)
        self.assertNotIn("| --- | --- |", rendered)

    def test_discord_formatter_preserves_normal_discord_markdown(self):
        source = "## Short answer\n\n- one\n- two\n\n```python\nprint('ok')\n```"
        self.assertEqual(_discordify(source), source)

    def test_personal_and_workspace_web_surfaces_mount_runtime_1_chat(self):
        shell = Path("apps/web/src/workspace-lite/WorkspaceSafeApp.tsx").read_text(encoding="utf-8")
        assistant = Path("apps/web/src/workspace/WorkspaceAssistantPanel.tsx").read_text(encoding="utf-8")
        personal = Path("apps/web/src/account/PersonalHome.tsx").read_text(encoding="utf-8")

        self.assertIn('import("../account/PersonalHome")', shell)
        self.assertIn('import("../workspace/WorkspaceOperly")', shell)
        self.assertIn('import("../workspace/WorkspaceAssistantPanel")', shell)
        self.assertIn('<PersonalHome profile={profile} onOpenSettings={() => openAccountSettings("account")} />', shell)
        self.assertIn('"/personal-agent/chat"', personal)
        self.assertIn('"/personal-agent/chat-with-attachments"', personal)
        self.assertIn('case "operly": return <WorkspaceOperly workspace={workspace} />;', shell)
        self.assertIn("<WorkspaceAssistantPanel workspace={selected}", shell)
        self.assertIn('"/agent/conversations"', assistant)
        self.assertIn('"/agent/chat"', assistant)
        self.assertIn('"/agent/chat-with-attachments"', assistant)


class InferenceSpendIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.tempdir = tempfile.TemporaryDirectory()
        database = Path(self.tempdir.name) / "inference-spend.db"
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{database}",
            connect_args={"timeout": 10},
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.tempdir.cleanup()

    def meter(self, *, task_limit: int = 100, max_calls: int = 12) -> AgentSpendMeter:
        return AgentSpendMeter(
            scope=SpendScope(
                scope_kind="personal",
                scope_id="user-1",
                task_id="task-provider-integration",
                project_id="conversation-1",
            ),
            run_id="runtime-provider-integration",
            session_factory=self.sessions,
            prices=PriceSnapshot.from_mapping(
                {
                    "version": "integration-fixture",
                    "models": {
                        "fixture:model": {
                            "input_micros_per_million_tokens": 0,
                            "output_micros_per_million_tokens": 1_000_000,
                        }
                    },
                }
            ),
            limits=SpendLimits(
                small_task_micros=task_limit,
                approved_composite_task_micros=max(task_limit, 500),
                scope_month_micros=10_000,
                project_month_micros=5_000,
                max_model_calls=max_calls,
            ),
        )

    def model(self, meter: AgentSpendMeter, *, max_attempts: int = 2) -> OpenAICompatibleAgentModel:
        return OpenAICompatibleAgentModel(
            route=InferenceRoute(
                provider="fixture",
                base_url="https://fixture.test/v1",
                api_key=None,
                model_id="model",
                max_output_tokens=20,
                max_attempts=max_attempts,
            ),
            spend_meter=meter,
        )

    async def reservations(self):
        async with self.sessions() as db:
            return list(
                (
                    await db.scalars(
                        select(AgentSpendReservationRecord).where(
                            AgentSpendReservationRecord.run_id == "runtime-provider-integration"
                        )
                    )
                ).all()
            )

    async def test_retry_reserves_each_dispatch_and_keeps_failed_attempt_uncertain(self):
        meter = self.meter()
        model = self.model(meter)
        responses = [
            StubResponse(429),
            StubResponse(
                200,
                {
                    "choices": [{"message": {"content": "done"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 4},
                },
            ),
        ]
        calls: list[dict] = []
        with patch(
            "packages.agent_runtime.inference.httpx.AsyncClient",
            side_effect=lambda *args, **kwargs: StubAsyncClient(responses, calls),
        ), patch("packages.agent_runtime.inference.asyncio.sleep", new=AsyncMock()):
            answer = await model.respond(objective="answer", user_message="hello")

        self.assertEqual(answer, "done")
        self.assertEqual(len(calls), 2)
        rows = await self.reservations()
        self.assertEqual(len(rows), 2)
        self.assertEqual({row.status for row in rows}, {"uncertain", "settled"})
        self.assertEqual({row.run_id for row in rows}, {"runtime-provider-integration"})
        task = next(row for row in await meter.budget_state() if row["kind"] == "task")
        self.assertEqual(task["calls_used"], 2)
        self.assertEqual(task["spent_micros"], 4)
        self.assertEqual(task["reserved_micros"], 20)

    async def test_success_without_provider_usage_remains_conservatively_reserved(self):
        meter = self.meter()
        model = self.model(meter, max_attempts=1)
        responses = [StubResponse(200, {"choices": [{"message": {"content": "done"}}]})]
        calls: list[dict] = []
        with patch(
            "packages.agent_runtime.inference.httpx.AsyncClient",
            side_effect=lambda *args, **kwargs: StubAsyncClient(responses, calls),
        ):
            answer = await model.respond(objective="answer", user_message="hello")

        self.assertEqual(answer, "done")
        self.assertEqual(len(calls), 1)
        rows = await self.reservations()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "uncertain")
        self.assertEqual(rows[0].uncertainty_reason, "provider_usage_missing")
        task = next(row for row in await meter.budget_state() if row["kind"] == "task")
        self.assertEqual(task["spent_micros"], 0)
        self.assertEqual(task["reserved_micros"], 20)
        self.assertEqual(task["calls_used"], 1)

    async def test_budget_exhaustion_blocks_provider_before_network_dispatch(self):
        meter = self.meter(task_limit=10)
        model = self.model(meter, max_attempts=1)
        responses = [
            StubResponse(
                200,
                {
                    "choices": [{"message": {"content": "should not run"}}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 1},
                },
            )
        ]
        calls: list[dict] = []
        with patch(
            "packages.agent_runtime.inference.httpx.AsyncClient",
            side_effect=lambda *args, **kwargs: StubAsyncClient(responses, calls),
        ):
            with self.assertRaises(AgentInferenceError) as caught:
                await model.respond(objective="answer", user_message="hello")
        self.assertEqual(caught.exception.code, "inference_spend_budget_exhausted")
        self.assertEqual(calls, [])

    def test_real_model_bind_uses_trusted_personal_scope_and_exact_run_id(self):
        model = OpenAICompatibleAgentModel(
            route=InferenceRoute(
                provider="ollama",
                base_url="http://127.0.0.1:11434/v1",
                api_key=None,
                model_id="fixture",
            )
        )
        model.bind_spend_context(context=personal_context(), run_id="trusted-run-123")
        self.assertIsNotNone(model.spend_meter)
        self.assertEqual(model.spend_meter.run_id, "trusted-run-123")
        self.assertEqual(model.spend_meter.scope.scope_kind, "personal")
        self.assertEqual(model.spend_meter.scope.scope_id, "user-1")
        self.assertEqual(model.spend_meter.scope.task_id, "trusted-run-123")

    def test_model_price_uses_exact_integer_ceiling_math(self):
        price = ModelPrice(333_333, 666_667)
        self.assertEqual(price.cost_micros(prompt_tokens=3, completion_tokens=3), 4)
        self.assertIsInstance(price.cost_micros(prompt_tokens=1, completion_tokens=1), int)


if __name__ == "__main__":
    unittest.main()
