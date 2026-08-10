import asyncio

import pytest

from packages.custom_software.live_planning import StructuredModelResult
from packages.custom_software.planning_orchestrator import (
    PlanningNeedsUserInput,
    RecursiveRepairPlanningOrchestrator,
)


TARGET_QUESTION = "Should this be a standalone application, integrated into the existing website, or an internal tool?"


class ClarifyingAnalystClient:
    provider = "fake"
    model_id = "fake-analyst"

    def __init__(self):
        self.calls = []

    async def generate_structured(self, *, role, context, output_schema, request_id, timeout_seconds, attempt=1):
        self.calls.append(role)
        if role != "requirements_analyst":
            raise AssertionError(f"planner should not be called after unresolved user questions: {role}")
        payload = {
            "root_objective": "Track arbitrary business state.",
            "requirements": [
                {
                    "requirement_id": "R-001",
                    "source_excerpt": "keep track of products",
                    "normalized_requirement": "Track products.",
                    "category": "Behavior",
                    "priority": "High",
                    "acceptance_criteria": ["Products can be tracked."],
                }
            ],
            "questions_requiring_user_input": [
                TARGET_QUESTION,
                "What is the technical nature of OPERLY, for example a specific API or third-party integration?",
            ],
        }
        validated = output_schema.model_validate(payload)
        return StructuredModelResult(
            provider=self.provider,
            model_id=self.model_id,
            request_id=request_id,
            attempt=attempt,
            latency_ms=1,
            input_tokens=10,
            output_tokens=10,
            structured_output=validated.model_dump(mode="json"),
            raw_response="{}",
            context_digest=context.digest(),
        )


def test_material_clarification_stops_recursive_planning_after_analyst():
    client = ClarifyingAnalystClient()
    orchestrator = RecursiveRepairPlanningOrchestrator(client)

    with pytest.raises(PlanningNeedsUserInput) as caught:
        asyncio.run(orchestrator.run("I need a capability, but do not assume where it should live."))

    assert client.calls == ["requirements_analyst"]
    assert caught.value.questions == [TARGET_QUESTION]
    assert orchestrator.budget.calls == 1
