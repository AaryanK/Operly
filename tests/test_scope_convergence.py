import asyncio

from packages.custom_software.live_planning import (
    PlanningContextPacket,
    StructuredModelResult,
    ValidatorOutput,
)
from packages.custom_software.scope_convergence import ScopeConvergingPlanningClient


class FakeValidatorClient:
    provider = "fake"
    model_id = "fake-validator"

    def __init__(self, verdict: ValidatorOutput):
        self.verdict = verdict

    async def generate_structured(self, **kwargs):
        return StructuredModelResult(
            provider=self.provider,
            model_id=self.model_id,
            request_id="req-1",
            attempt=1,
            latency_ms=1,
            structured_output=self.verdict.model_dump(mode="json"),
            context_digest=kwargs["context"].digest(),
        )


def verdict(**overrides):
    data = {
        "disposition": "prune",
        "ready_for_implementation": True,
        "semantic_coverage": "complete",
        "irrelevant_scope_expansion": ["error types"],
        "reasoning_summary": "error behavior is complete",
    }
    data.update(overrides)
    return ValidatorOutput.model_validate(data)


def context(previous=True):
    return PlanningContextPacket(
        role="validator",
        untrusted_requirements={
            "linked": [
                {
                    "requirement_id": "R-004",
                    "source_excerpt": "Show a clear error for invalid input and division by zero.",
                    "normalized_requirement": "Handle invalid input and division by zero clearly.",
                }
            ]
        },
        current_contract={"node_id": "part_error_types"},
        constraints={"deterministic_scope_findings": []},
        previous_findings=(
            [{"disposition": "prune", "irrelevant_scope_expansion": ["error types"]}]
            if previous
            else []
        ),
    )


def test_repeated_required_error_scope_converges_to_approval():
    client = ScopeConvergingPlanningClient(FakeValidatorClient(verdict()))
    result = asyncio.run(
        client.generate_structured(
            role="validator",
            context=context(previous=True),
            output_schema=ValidatorOutput,
            request_id="req-1",
            timeout_seconds=5,
        )
    )
    corrected = ValidatorOutput.model_validate(result.structured_output)
    assert corrected.disposition == "approve"
    assert result.retry_history[-1]["controller"] == "scope_convergence"
    assert "error types" in result.retry_history[-1]["protected_requirement_scope"]


def test_first_actionable_implementation_scope_is_not_bypassed():
    client = ScopeConvergingPlanningClient(
        FakeValidatorClient(verdict(irrelevant_scope_expansion=["database storage"]))
    )
    result = asyncio.run(
        client.generate_structured(
            role="validator",
            context=context(previous=False),
            output_schema=ValidatorOutput,
            request_id="req-1",
            timeout_seconds=5,
        )
    )
    corrected = ValidatorOutput.model_validate(result.structured_output)
    assert corrected.disposition == "prune"
