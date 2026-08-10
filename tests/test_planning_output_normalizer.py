import asyncio
import json

from packages.custom_software.live_planning import (
    FailureClass,
    PlannerOutput,
    PlanningContextPacket,
    StructuredModelResult,
)
from packages.custom_software.planning_output_normalizer import (
    NormalizingPlanningClient,
    normalize_planner_payload,
)


def malformed_planner_payload():
    return {
        "nodes": [
            {
                "node_id": "ROOT",
                "title": "Calculator UI",
                "node_type": "Component",
                "objective": "Provide the requested calculator website",
                "responsibilities": ["Render calculator controls"],
                "linked_requirement_ids": ["R-001"],
                "scope_claims": [
                    {
                        "subject": "Web-based user interface",
                        "authority": "derived_essential_requirement",
                        "linked_requirement_ids": [],
                        "justification": "",
                        "blocks_readiness": True,
                    }
                ],
                "children": [],
            }
        ]
    }


def test_unsupported_essential_claim_is_demoted_without_inventing_evidence():
    normalized = normalize_planner_payload(malformed_planner_payload())
    claim = normalized["nodes"][0]["scope_claims"][0]
    assert claim["authority"] == "implementation_choice"
    assert claim["linked_requirement_ids"] == []
    assert claim["blocks_readiness"] is False
    assert "no complete evidence" in claim["justification"]
    PlannerOutput.model_validate(normalized)


class FailingPlannerClient:
    provider = "ollama"
    model_id = "test-model"

    async def generate_structured(self, **kwargs):
        return StructuredModelResult(
            provider=self.provider,
            model_id=self.model_id,
            request_id=kwargs["request_id"],
            attempt=kwargs.get("attempt", 1),
            latency_ms=1,
            raw_response=json.dumps(malformed_planner_payload()),
            validation_errors=["essential derivations require linked requirements and justification"],
            failure_classification=FailureClass.SCHEMA_MISMATCH,
            context_digest=kwargs["context"].digest(),
        )


def test_wrapper_recovers_schema_mismatch_in_same_attempt():
    client = NormalizingPlanningClient(FailingPlannerClient())
    context = PlanningContextPacket(
        role="planner",
        untrusted_requirements={"requirements": [{"requirement_id": "R-001"}]},
    )
    result = asyncio.run(
        client.generate_structured(
            role="planner",
            context=context,
            output_schema=PlannerOutput,
            request_id="request-1",
            timeout_seconds=5,
            attempt=1,
        )
    )
    assert result.failure_classification is None
    assert result.validation_errors == []
    claim = result.structured_output["nodes"][0]["scope_claims"][0]
    assert claim["authority"] == "implementation_choice"
    assert claim["blocks_readiness"] is False
