from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

from packages.agent_runtime.context import ContextSlice
from packages.agent_runtime.evaluation import (
    NO_TOOL_AND_SCOPE_CASES,
    OBJECTIVE_EVAL_CASES,
    _evaluate_case,
    _execution_context,
    _interpret_with_backoff,
    run_startup_objective_eval_if_enabled,
)
from packages.agent_runtime.inference import (
    OBJECTIVE_SEMANTIC_ROUTING_GUIDANCE,
    InferenceRoute,
    OpenAICompatibleAgentModel,
)
from packages.agent_runtime.objective import (
    ObjectiveComplexity,
    ObjectiveIR,
    ObjectiveInterpretationError,
    ObjectiveInterpreterRequest,
    ObjectiveKind,
    ObjectiveOperation,
)


class ObjectiveEvaluationHarnessTests(unittest.IsolatedAsyncioTestCase):
    def test_corpus_is_broad_raw_and_unique(self):
        self.assertGreaterEqual(len(OBJECTIVE_EVAL_CASES), 50)
        ids = [case.case_id for case in OBJECTIVE_EVAL_CASES]
        self.assertEqual(len(ids), len(set(ids)))
        prompts = "\n".join(case.prompt.lower() for case in OBJECTIVE_EVAL_CASES)
        for fragment in ("tmrw", "emial", "3ish", "dont send it", "keep an eye", "sum that up"):
            self.assertIn(fragment, prompts)
        kinds = {case.kind for case in OBJECTIVE_EVAL_CASES}
        self.assertTrue({"respond", "retrieve", "act", "composite", "wait"}.issubset(kinds))

    def test_fast_slice_covers_no_tool_behavior_in_both_scopes(self):
        personal_no_tool = [case for case in NO_TOOL_AND_SCOPE_CASES if case.scope == "personal" and not case.external_state]
        workspace_no_tool = [case for case in NO_TOOL_AND_SCOPE_CASES if case.scope == "workspace" and not case.external_state]
        self.assertGreaterEqual(len(personal_no_tool), 6)
        self.assertGreaterEqual(len(workspace_no_tool), 8)
        self.assertTrue(all(case.kind == "respond" and case.dispatch == "respond" for case in personal_no_tool + workspace_no_tool))
        prompts = "\n".join(case.prompt.lower() for case in workspace_no_tool)
        for smb_term in ("cash flow", "gross margin", "pricing", "customer", "saas", "inventory"):
            self.assertIn(smb_term, prompts)

    def test_workspace_boundary_cases_expect_workspace_capabilities(self):
        cases = [case for case in NO_TOOL_AND_SCOPE_CASES if case.scope == "workspace" and case.external_state]
        expected = {case.expected_capability for case in cases}
        self.assertIn("workspace.search", expected)
        self.assertIn("workspace.attention.list", expected)
        self.assertIn("workspace.finance.invoice.create_simple", expected)
        self.assertIn("workspace.customer.snapshot", expected)

    def test_scorecard_checks_classifier_and_tool_hit(self):
        case = next(case for case in OBJECTIVE_EVAL_CASES if case.case_id == "gmail.search.dad")
        objective = ObjectiveIR(
            objective="Find emails from Dad",
            kind=ObjectiveKind.RETRIEVE,
            operations=(ObjectiveOperation.RETRIEVE,),
            resource_hints=("email",),
            requires_external_state=True,
            requires_mutation=False,
            requires_future_wait=False,
            complexity=ObjectiveComplexity.SIMPLE,
        )
        ok, mismatches = _evaluate_case(
            case,
            objective,
            ["google.gmail.search", "google.gmail.read_message"],
        )
        self.assertTrue(ok)
        self.assertEqual(mismatches, [])

        ok, mismatches = _evaluate_case(case, objective, ["google.gmail.send_email"])
        self.assertFalse(ok)
        self.assertIn("tool_missing:google.gmail.search", mismatches)

    async def test_model_failure_retries_with_bounded_backoff(self):
        case = OBJECTIVE_EVAL_CASES[0]
        expected = ObjectiveIR(
            objective="Explain invoices and receipts",
            kind=ObjectiveKind.RESPOND,
            operations=(ObjectiveOperation.RESPOND,),
            resource_hints=(),
            requires_external_state=False,
            requires_mutation=False,
            requires_future_wait=False,
            complexity=ObjectiveComplexity.SIMPLE,
        )
        interpreter = AsyncMock()
        interpreter.interpret.side_effect = [
            ObjectiveInterpretationError("provider unavailable", code="objective_model_failed"),
            expected,
        ]
        with patch("packages.agent_runtime.evaluation.asyncio.sleep", new=AsyncMock()) as sleep:
            result = await _interpret_with_backoff(
                interpreter,
                case=case,
                context=_execution_context("personal"),
                max_attempts=3,
                retry_delay_seconds=7.0,
            )
        self.assertIs(result, expected)
        self.assertEqual(interpreter.interpret.await_count, 2)
        sleep.assert_awaited_once_with(7.0)

    async def test_semantic_contract_error_is_not_retried(self):
        case = OBJECTIVE_EVAL_CASES[0]
        interpreter = AsyncMock()
        interpreter.interpret.side_effect = ObjectiveInterpretationError(
            "invalid semantic output",
            code="inconsistent_objective_output",
        )
        with patch("packages.agent_runtime.evaluation.asyncio.sleep", new=AsyncMock()) as sleep:
            with self.assertRaises(ObjectiveInterpretationError):
                await _interpret_with_backoff(
                    interpreter,
                    case=case,
                    context=_execution_context("personal"),
                    max_attempts=3,
                    retry_delay_seconds=7.0,
                )
        self.assertEqual(interpreter.interpret.await_count, 1)
        sleep.assert_not_awaited()

    async def test_startup_eval_is_strictly_opt_in(self):
        with patch.dict(os.environ, {"OPERLY_AGENT_OBJECTIVE_EVAL_ON_START": "0"}, clear=False):
            self.assertIsNone(await run_startup_objective_eval_if_enabled())

    async def test_interpreter_uses_semantic_contrastive_guidance(self):
        model = OpenAICompatibleAgentModel(
            route=InferenceRoute(
                provider="groq",
                base_url="https://api.groq.com/openai/v1",
                api_key=None,
                model_id="openai/gpt-oss-120b",
            )
        )
        model._chat = AsyncMock(return_value='{"objective":"x"}')
        request = ObjectiveInterpreterRequest(
            message="what did dad email me last",
            scope_kind="personal",
            surface="personal_private",
            relevant_context=ContextSlice(items=(), total_bytes=0, omitted_count=0),
        )
        await model.interpret(request)
        system = model._chat.await_args.kwargs["system"]
        self.assertIn(OBJECTIVE_SEMANTIC_ROUTING_GUIDANCE, system)
        self.assertIn("Classify the meaning of the whole request", system)
        self.assertIn("what did dad email me last?", system)
        self.assertIn("reply yes to dad's latest email", system)
        self.assertIn("move whatever meeting", system)
        self.assertIn("tell me when dad replies", system)


if __name__ == "__main__":
    unittest.main()
