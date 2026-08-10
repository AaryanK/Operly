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


@pytest.mark.parametrize(
    "question",
    [
        "What are the specific four services to be offered?",
        "What are the defined pet size categories?",
        "What specific fields are required for products and suppliers?",
        "Which specific summary metrics are required?",
        "What are the specific severity levels required for incident creation?",
        "What belongs in the automatically generated public-status summary?",
        "What is the pricing logic/matrix for services and pet sizes?",
        "What specific contact details are required (e.g., email, phone, address)?",
        "Which specific data sets should be included in the CSV export?",
        "How should pipeline value be calculated specifically?",
        "Which browser persistence mechanism is preferred?",
    ],
)
def test_conventional_product_defaults_do_not_interrupt_planning(question):
    from packages.custom_software.graph_planning import material_user_questions

    assert material_user_questions([question]) == []


def test_security_and_ownership_questions_remain_owner_decisions():
    from packages.custom_software.graph_planning import material_user_questions

    questions = [
        "What specific permission level should contractors receive?",
        "Which jurisdiction controls the regulated customer data?",
        "Who has data ownership for submitted health records?",
    ]
    assert material_user_questions(questions) == questions[:2]


def test_model_invented_compliance_question_is_suppressed_when_request_has_no_risk_constraint():
    from packages.custom_software.graph_planning import material_user_questions

    question = "Who owns the data and are there specific legal or compliance requirements?"
    prompt = "Build an incident dashboard with browser persistence and status updates."
    assert material_user_questions([question], prompt) == []


def test_explicit_compliance_requirement_can_still_require_owner_input():
    from packages.custom_software.graph_planning import material_user_questions

    question = "Which compliance requirements govern the incident logs?"
    prompt = "The incident logs must comply with our compliance requirements, but the governing standard is unspecified."
    assert material_user_questions([question], prompt) == [question]
