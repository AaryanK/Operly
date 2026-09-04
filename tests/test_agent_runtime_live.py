from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from packages.agent_runtime.context import ContextItem, ContextKind
from packages.agent_runtime.inference import AgentInferenceError, InferenceRoute
from packages.agent_runtime.interactive import Runtime1Agent
from packages.agent_runtime.runtime import AgentRuntimeSettings
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind


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
                "GROQ_API_KEY": "test-key",
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


if __name__ == "__main__":
    unittest.main()
