from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from packages.agent_runtime.context import ContextItem, ContextKind
from packages.agent_runtime.inference import (
    AgentInferenceError,
    InferenceRoute,
    OpenAICompatibleAgentModel,
)
from packages.agent_runtime.interactive import Runtime1Agent
from packages.agent_runtime.runtime import AgentRuntimeSettings
from packages.security.execution_context import ExecutionContext, ScopeKind
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


def personal_context() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=None,
        user_id="user-1",
        membership_id=None,
        role="personal_owner",
        permissions=frozenset({"workspace:read"}),
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

        self.assertIn('import("../account/PersonalHome")', shell)
        self.assertIn('import("../workspace/WorkspaceOperly")', shell)
        self.assertIn('import("../workspace/WorkspaceAssistantPanel")', shell)
        self.assertIn("<PersonalHome profile={null} />", shell)
        self.assertIn('case "operly": return <WorkspaceOperly workspace={workspace} />;', shell)
        self.assertIn("<WorkspaceAssistantPanel workspace={selected}", shell)
        self.assertIn('"/agent/conversations"', assistant)
        self.assertIn('"/agent/chat"', assistant)
        self.assertIn('"/agent/chat-with-attachments"', assistant)


if __name__ == "__main__":
    unittest.main()
