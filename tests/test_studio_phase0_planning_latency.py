from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from packages.custom_software.live_planning import PlanningContextPacket
from packages.custom_software.model_planning_client import (
    ModelPlanningClient,
    _planning_call_timeout,
    _planning_max_models,
)
from packages.model_runtime import InferenceResult


class TinyOutput(BaseModel):
    value: str


def test_phase0_planning_role_deadlines_are_bounded_by_default():
    with patch.dict(os.environ, {}, clear=True):
        assert _planning_call_timeout("requirements_analyst", 120) == 60
        assert _planning_call_timeout("planner", 120) == 90
        assert _planning_call_timeout("global_validator", 120) == 60
        assert _planning_max_models() == 2


def test_phase0_planning_deadline_never_exceeds_orchestrator_request():
    with patch.dict(
        os.environ,
        {
            "OPERLY_PLANNING_PLANNER_TIMEOUT_SECONDS": "999",
            "OPERLY_PLANNING_MAX_MODELS": "99",
        },
        clear=True,
    ):
        assert _planning_call_timeout("planner", 47) == 47
        assert _planning_max_models() == 3


@pytest.mark.asyncio
async def test_planning_client_passes_bounded_candidate_budget_to_model_runtime():
    observed = {}

    class FakeModel:
        id = "role:planner:test"

        async def infer(self, request):
            observed["budget"] = request.budget
            return InferenceResult(
                message={"role": "assistant", "content": '{"value":"ok"}'},
                model_resource_id="test-resource",
                provider="test-provider",
                provider_model_id="test-model",
                latency_ms=3,
            )

    client = ModelPlanningClient(model_resolver=lambda _role: FakeModel())
    context = PlanningContextPacket(
        role="planner",
        untrusted_requirements={"request": "test"},
    )
    with patch.dict(os.environ, {}, clear=True):
        result = await client.generate_structured(
            role="planner",
            context=context,
            output_schema=TinyOutput,
            request_id="test-request",
            timeout_seconds=120,
        )

    assert result.structured_output == {"value": "ok"}
    assert observed["budget"].timeout_seconds == 60
    assert observed["budget"].attempts_per_model == 1
    assert observed["budget"].max_models == 2
