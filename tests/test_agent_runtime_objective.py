from __future__ import annotations

import unittest

from packages.agent_runtime import (
    AgentRuntimeDisabled,
    AgentRuntimeSettings,
    ContextAssembler,
    ContextBudget,
    ContextItem,
    ContextKind,
    ObjectiveInterpretationError,
    ObjectiveInterpreter,
    ObjectiveInterpreterLimits,
    RuntimeDispatchPath,
)
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind


def execution_context() -> ExecutionContext:
    return ExecutionContext(
        workspace_id="workspace-1",
        user_id="user-1",
        membership_id="membership-1",
        role="member",
        permissions=frozenset({"calendar:read", "calendar:write", "tasks:write"}),
        channel="web",
        surface=SurfaceKind.WORKSPACE_PRIVATE,
        conversation_id="conversation-1",
        scope_kind=ScopeKind.WORKSPACE,
        principal_id="user:user-1",
    )


def output(
    *,
    objective: str,
    kind: str,
    operations: list[str],
    resource_hints: list[str] | None = None,
    external: bool = False,
    mutation: bool = False,
    future: bool = False,
    complexity: str = "simple",
) -> dict:
    return {
        "objective": objective,
        "kind": kind,
        "operations": operations,
        "resource_hints": resource_hints or [],
        "requires_external_state": external,
        "requires_mutation": mutation,
        "requires_future_wait": future,
        "complexity": complexity,
    }


class FakeObjectiveModel:
    def __init__(self, result) -> None:
        self.result = result
        self.requests = []

    async def interpret(self, request):
        self.requests.append(request)
        return self.result


class AgentRuntimeObjectiveTests(unittest.IsolatedAsyncioTestCase):
    def interpreter(self, result, *, limits: ObjectiveInterpreterLimits | None = None):
        model = FakeObjectiveModel(result)
        return (
            ObjectiveInterpreter(
                model=model,
                settings=AgentRuntimeSettings(enabled=True),
                limits=limits,
            ),
            model,
        )

    async def test_disabled_by_default(self):
        model = FakeObjectiveModel(
            output(
                objective="Explain recursion",
                kind="respond",
                operations=["respond"],
            )
        )
        interpreter = ObjectiveInterpreter(model=model)
        with self.assertRaises(AgentRuntimeDisabled):
            await interpreter.interpret(
                message="What is recursion?",
                context=execution_context(),
            )
        self.assertEqual(model.requests, [])

    async def test_runtime_1_reference_route_scorecard(self):
        cases = (
            (
                "Explain recursion",
                output(
                    objective="Explain recursion",
                    kind="respond",
                    operations=["analyze", "respond"],
                ),
                RuntimeDispatchPath.RESPOND,
            ),
            (
                "What meetings do I have tomorrow?",
                output(
                    objective="Find tomorrow's meetings",
                    kind="retrieve",
                    operations=["retrieve", "respond"],
                    resource_hints=["calendar"],
                    external=True,
                ),
                RuntimeDispatchPath.DIRECT_CAPABILITY,
            ),
            (
                "Move my 3 PM meeting to Friday",
                output(
                    objective="Move the 3 PM meeting to Friday",
                    kind="act",
                    operations=["act"],
                    resource_hints=["calendar"],
                    external=True,
                    mutation=True,
                ),
                RuntimeDispatchPath.DIRECT_CAPABILITY,
            ),
            (
                "Find launch blockers and create tasks for the important ones",
                output(
                    objective="Identify launch blockers and create remediation tasks",
                    kind="composite",
                    operations=["retrieve", "analyze", "act"],
                    resource_hints=["issues", "tasks"],
                    external=True,
                    mutation=True,
                    complexity="compound",
                ),
                RuntimeDispatchPath.AGENT_LOOP,
            ),
            (
                "Watch for a reply from the vendor",
                output(
                    objective="Wait for a vendor reply",
                    kind="wait",
                    operations=["wait", "retrieve"],
                    resource_hints=["email"],
                    external=True,
                    future=True,
                ),
                RuntimeDispatchPath.WAIT,
            ),
        )

        passed = 0
        for message, result, expected in cases:
            interpreter, _ = self.interpreter(result)
            ir = await interpreter.interpret(
                message=message,
                context=execution_context(),
            )
            if ir.dispatch_path() is expected:
                passed += 1
        self.assertEqual(passed, len(cases))

    async def test_no_tool_is_first_class_for_reasoning_and_transform(self):
        result = output(
            objective="Rewrite the supplied note more clearly",
            kind="respond",
            operations=["transform", "respond"],
            external=False,
        )
        interpreter, model = self.interpreter(result)
        ir = await interpreter.interpret(
            message="Make this clearer",
            context=execution_context(),
            context_items=(
                ContextItem(
                    key="note-1",
                    kind=ContextKind.USER_PROVIDED,
                    text="Make the launch note shorter and clearer",
                    relevance=1.0,
                ),
            ),
        )
        self.assertFalse(ir.needs_capability_discovery)
        self.assertEqual(ir.capability_query(), "")
        self.assertIs(ir.dispatch_path(), RuntimeDispatchPath.RESPOND)
        self.assertEqual(len(model.requests[0].relevant_context.items), 1)

    async def test_context_selection_is_relevance_and_byte_bounded(self):
        limits = ObjectiveInterpreterLimits(
            context_budget=ContextBudget(
                max_items=2,
                max_bytes=700,
                max_item_bytes=400,
            )
        )
        interpreter, model = self.interpreter(
            output(
                objective="Summarize the Acme launch status",
                kind="respond",
                operations=["analyze", "respond"],
            ),
            limits=limits,
        )
        items = (
            ContextItem(
                key="relevant-memory",
                kind=ContextKind.MEMORY,
                text="The Acme launch review is scheduled for Friday.",
                relevance=0.95,
            ),
            ContextItem(
                key="matching-conversation",
                kind=ContextKind.CONVERSATION,
                text="We discussed the Acme launch blocker yesterday.",
            ),
            ContextItem(
                key="unrelated",
                kind=ContextKind.MEMORY,
                text="Favorite lunch is ramen.",
            ),
            ContextItem(
                key="oversized",
                kind=ContextKind.ARTIFACT,
                text="Acme " + ("x" * 1000),
                relevance=1.0,
            ),
        )
        await interpreter.interpret(
            message="What is going on with the Acme launch?",
            context=execution_context(),
            context_items=items,
        )
        selected = model.requests[0].relevant_context
        keys = [item.key for item in selected.items]
        self.assertEqual(keys, ["relevant-memory", "matching-conversation"])
        self.assertLessEqual(selected.total_bytes, 700)
        self.assertEqual(selected.omitted_count, 2)

        request_payload = model.requests[0].as_dict()
        serialized = str(request_payload)
        self.assertNotIn("relevant-memory", serialized)
        self.assertNotIn("workspace-1", serialized)
        self.assertNotIn("user-1", serialized)
        self.assertNotIn("calendar:write", serialized)

    async def test_capability_query_uses_compact_ir_not_raw_context(self):
        interpreter, _ = self.interpreter(
            output(
                objective="Find tomorrow's project meeting",
                kind="retrieve",
                operations=["retrieve", "respond"],
                resource_hints=["calendar"],
                external=True,
            )
        )
        ir = await interpreter.interpret(
            message="What is my project meeting tomorrow? SECRET-FULL-CONVERSATION",
            context=execution_context(),
            context_items=(
                ContextItem(
                    key="secret",
                    kind=ContextKind.CONVERSATION,
                    text="SECRET-FULL-CONVERSATION should not be copied into capability discovery",
                    relevance=1.0,
                ),
            ),
        )
        query = ir.capability_query()
        self.assertIn("Find tomorrow's project meeting", query)
        self.assertIn("calendar", query)
        self.assertNotIn("SECRET-FULL-CONVERSATION", query)

    async def test_authority_shaped_model_fields_fail_closed(self):
        hostile = output(
            objective="Read calendar",
            kind="retrieve",
            operations=["retrieve"],
            resource_hints=["calendar"],
            external=True,
        )
        hostile["workspace_id"] = "other-workspace"
        interpreter, _ = self.interpreter(hostile)
        with self.assertRaises(ObjectiveInterpretationError) as caught:
            await interpreter.interpret(
                message="Read my calendar",
                context=execution_context(),
            )
        self.assertEqual(caught.exception.code, "objective_authority_violation")

    async def test_inconsistent_mutation_without_external_state_is_rejected(self):
        interpreter, _ = self.interpreter(
            output(
                objective="Do a write",
                kind="respond",
                operations=["act"],
                external=False,
                mutation=True,
            )
        )
        with self.assertRaises(ObjectiveInterpretationError) as caught:
            await interpreter.interpret(
                message="Do it",
                context=execution_context(),
            )
        self.assertEqual(caught.exception.code, "inconsistent_objective_output")

    async def test_malformed_markdown_and_oversized_output_are_rejected(self):
        malformed, _ = self.interpreter(
            "```json\n{\"objective\":\"x\"}\n```"
        )
        with self.assertRaises(ObjectiveInterpretationError) as caught:
            await malformed.interpret(
                message="hello",
                context=execution_context(),
            )
        self.assertEqual(caught.exception.code, "invalid_objective_output")

        oversized, _ = self.interpreter(
            "x" * 400,
            limits=ObjectiveInterpreterLimits(max_output_bytes=128),
        )
        with self.assertRaises(ObjectiveInterpretationError) as caught:
            await oversized.interpret(
                message="hello",
                context=execution_context(),
            )
        self.assertEqual(caught.exception.code, "objective_output_too_large")

    def test_context_assembler_can_return_empty_slice(self):
        assembler = ContextAssembler()
        result = assembler.select(
            "calendar",
            (
                ContextItem(
                    key="irrelevant",
                    kind=ContextKind.MEMORY,
                    text="ramen lunch",
                ),
            ),
            budget=ContextBudget(max_items=2, max_bytes=500, max_item_bytes=400),
        )
        self.assertEqual(result.items, ())
        self.assertEqual(result.omitted_count, 1)


if __name__ == "__main__":
    unittest.main()
