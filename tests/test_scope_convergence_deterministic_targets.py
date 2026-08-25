import asyncio

from packages.software_projects.planning.live_planning import PlanningContextPacket, StructuredModelResult
from packages.software_projects.planning.scope_convergence import ScopeConvergingPlanningClient


class FakeValidator:
    provider = "fake"
    model_id = "fake"

    async def generate_structured(self, **kwargs):
        context = kwargs["context"]
        return StructuredModelResult(
            provider="fake",
            model_id="fake",
            request_id="scope-test",
            attempt=1,
            input_tokens=100,
            output_tokens=50,
            latency_ms=1,
            context_digest=context.digest(),
            structured_output={
                "disposition": "prune",
                "ready_for_implementation": False,
                "semantic_coverage": "linked requirement covered",
                "missing_information": [],
                "ambiguous_behavior": [],
                "missing_inputs": [],
                "missing_outputs": [],
                "missing_invariants": [],
                "missing_dependencies": [],
                "missing_failure_handling": [],
                "missing_security_rules": [],
                "missing_persistence_behavior": [],
                "missing_tests": [],
                "requirement_conflicts": [],
                "irrelevant_concepts": [],
                "irrelevant_scope_expansion": ["persistent storage"],
                "minimal_contract_guidance": [],
                "finding_ids": [],
                "fields_to_patch": [],
                "fields_to_preserve": [],
                "recommended_decomposition": [],
                "reasoning_summary": "Remove unsupported storage mechanism.",
            },
        )


def test_deterministic_scope_mechanism_is_added_to_first_minimal_replacement():
    context = PlanningContextPacket(
        role="validator",
        untrusted_requirements={
            "linked": [
                {
                    "requirement_id": "R-001",
                    "source_excerpt": "Keep the board state so I can return later.",
                    "normalized_requirement": "Persist board state.",
                }
            ]
        },
        current_contract={},
        constraints={"deterministic_scope_findings": ["unjustified scope expansion: database"]},
        previous_findings=[],
    )
    result = asyncio.run(
        ScopeConvergingPlanningClient(FakeValidator()).generate_structured(
            role="validator",
            context=context,
            output_schema=object,
            request_id="scope-test",
            timeout_seconds=1,
        )
    )
    verdict = result.structured_output
    assert verdict["disposition"] == "replace_with_minimal_contract"
    assert "database" in verdict["irrelevant_scope_expansion"]
    assert any(item.get("reason") == "merge_deterministic_scope_targets" for item in result.retry_history)
