from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

from packages.agent_runtime.evaluation import (
    OBJECTIVE_EVAL_CASES,
    _evaluate_case,
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
    ObjectiveKind,
    ObjectiveOperation,
    ObjectiveInterpreterRequest,
)
from packages.agent_runtime.context import ContextSlice


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
