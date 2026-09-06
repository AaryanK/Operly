from __future__ import annotations

import unittest

from packages.agent_runtime.objective import (
    ObjectiveInterpretationError,
    ObjectiveInterpreter,
    ObjectiveKind,
    ObjectiveOperation,
)
from packages.agent_runtime.runtime import AgentRuntimeSettings
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind


def context() -> ExecutionContext:
    return ExecutionContext(
        workspace_id="workspace-1",
        user_id="user-1",
        membership_id="membership-1",
        role="owner",
        permissions=frozenset(),
        channel="web",
        surface=SurfaceKind.WORKSPACE_PRIVATE,
        conversation_id="conversation-1",
        scope_kind=ScopeKind.WORKSPACE,
        principal_id="user:user-1",
    )


def objective_payload(
    *,
    external: bool,
    mutation: bool,
    kind: str = "act",
    operations: list[str] | None = None,
) -> dict:
    return {
        "objective": "Create a 500 dollar invoice for design work",
        "kind": kind,
        "operations": operations or ["act"],
        "resource_hints": ["invoice", "finance"],
        "requires_external_state": external,
        "requires_mutation": mutation,
        "requires_future_wait": False,
        "complexity": "simple",
    }


class SequenceModel:
    def __init__(self, *outputs) -> None:
        self.outputs = list(outputs)
        self.requests = []

    async def interpret(self, request):
        self.requests.append(request)
        if not self.outputs:
            raise AssertionError("unexpected extra model call")
        return self.outputs.pop(0)


class ObjectiveSemanticRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_inconsistent_mutation_gets_one_bounded_semantic_repair(self):
        model = SequenceModel(
            objective_payload(external=False, mutation=True),
            objective_payload(external=True, mutation=True),
        )
        interpreter = ObjectiveInterpreter(
            model=model,
            settings=AgentRuntimeSettings(enabled=True),
        )
        result = await interpreter.interpret(
            message="create a $500 invoice for design work due in 14 days",
            context=context(),
        )
        self.assertIs(result.kind, ObjectiveKind.ACT)
        self.assertIn(ObjectiveOperation.ACT, result.operations)
        self.assertTrue(result.requires_external_state)
        self.assertTrue(result.requires_mutation)
        self.assertEqual(len(model.requests), 2)
        self.assertIn("previous semantic classification was rejected", model.requests[1].instructions.lower())
        self.assertEqual(model.requests[0].scope_kind, model.requests[1].scope_kind)
        self.assertEqual(model.requests[0].surface, model.requests[1].surface)

    async def test_authority_violation_never_gets_repaired(self):
        hostile = objective_payload(external=True, mutation=True)
        hostile["workspace_id"] = "other-workspace"
        model = SequenceModel(hostile, objective_payload(external=True, mutation=True))
        interpreter = ObjectiveInterpreter(
            model=model,
            settings=AgentRuntimeSettings(enabled=True),
        )
        with self.assertRaises(ObjectiveInterpretationError) as caught:
            await interpreter.interpret(
                message="create an invoice",
                context=context(),
            )
        self.assertEqual(caught.exception.code, "objective_authority_violation")
        self.assertEqual(len(model.requests), 1)

    async def test_second_inconsistent_result_still_fails_closed(self):
        bad = objective_payload(external=False, mutation=True)
        model = SequenceModel(bad, bad)
        interpreter = ObjectiveInterpreter(
            model=model,
            settings=AgentRuntimeSettings(enabled=True),
        )
        with self.assertRaises(ObjectiveInterpretationError) as caught:
            await interpreter.interpret(
                message="create an invoice",
                context=context(),
            )
        self.assertEqual(caught.exception.code, "inconsistent_objective_output")
        self.assertEqual(len(model.requests), 2)


if __name__ == "__main__":
    unittest.main()
