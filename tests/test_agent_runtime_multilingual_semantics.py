from __future__ import annotations

import unittest

from packages.agent_runtime.objective import ObjectiveInterpreter
from packages.agent_runtime.runtime import AgentRuntimeSettings
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind


def context() -> ExecutionContext:
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


class RecordingModel:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.requests = []

    async def interpret(self, request):
        self.requests.append(request)
        return self.result


def payload(*, objective: str, kind: str, operations: list[str], resources: list[str], external: bool, mutation: bool = False) -> dict:
    return {
        "objective": objective,
        "kind": kind,
        "operations": operations,
        "resource_hints": resources,
        "requires_external_state": external,
        "requires_mutation": mutation,
        "requires_future_wait": False,
        "complexity": "simple",
    }


class MultilingualSemanticCompilationTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_english_request_compiles_to_canonical_retrieval_query(self):
        model = RecordingModel(
            payload(
                objective="Search mail messages from Dad about the flight",
                kind="retrieve",
                operations=["retrieve"],
                resources=["email", "message"],
                external=True,
            )
        )
        interpreter = ObjectiveInterpreter(
            model=model,
            settings=AgentRuntimeSettings(enabled=True),
        )
        ir = await interpreter.interpret(
            message="cherche les e-mails de papa au sujet de mon vol",
            context=context(),
        )
        query = ir.capability_query()
        self.assertIn("Search mail messages", query)
        self.assertIn("resources email message", query)
        self.assertNotIn("cherche", query)
        self.assertNotIn("vol", query)
        instructions = model.requests[0].instructions.lower()
        self.assertIn("any language", instructions)
        self.assertIn("canonical english", instructions)
        self.assertIn("not hard routing", instructions)

    async def test_code_switched_request_uses_same_machine_semantics(self):
        model = RecordingModel(
            payload(
                objective="Read calendar availability Friday around 3 PM",
                kind="retrieve",
                operations=["retrieve"],
                resources=["calendar", "availability"],
                external=True,
            )
        )
        interpreter = ObjectiveInterpreter(
            model=model,
            settings=AgentRuntimeSettings(enabled=True),
        )
        ir = await interpreter.interpret(
            message="check si je suis free vendredi vers 3pm",
            context=context(),
        )
        self.assertEqual(
            ir.capability_query(),
            "Read calendar availability Friday around 3 PM | resources calendar availability | operations retrieve",
        )

    async def test_general_non_english_question_stays_no_tool(self):
        model = RecordingModel(
            payload(
                objective="Explain the difference between cash flow and profit",
                kind="respond",
                operations=["respond"],
                resources=[],
                external=False,
            )
        )
        interpreter = ObjectiveInterpreter(
            model=model,
            settings=AgentRuntimeSettings(enabled=True),
        )
        ir = await interpreter.interpret(
            message="explique simplement la différence entre flux de trésorerie et bénéfice",
            context=context(),
        )
        self.assertFalse(ir.requires_external_state)
        self.assertEqual(ir.capability_query(), "")


if __name__ == "__main__":
    unittest.main()
