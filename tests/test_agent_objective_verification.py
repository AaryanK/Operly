from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import packages.agents.controller as controller_module
from packages.agents.controller import AgentRunController
from packages.agents.run_state import RunPlan, RunTask
from packages.agents.verification import RunGoalVerification


class FakePlanner:
    async def plan(self, objective, *, trace_metadata=None):
        del trace_metadata
        return RunPlan(
            objective=objective,
            success_criteria=(
                "Create an XLSX with the requested contact columns.",
                "Create a Gmail draft with the generated PDF actually attached.",
            ),
            tasks=[RunTask(id="task-1", objective=objective)],
            planning_required=True,
            revision=0,
        )

    async def replan(self, state, *, reason, trace_metadata=None):
        del reason, trace_metadata
        plan = state.plan
        assert plan is not None
        return RunPlan(
            objective=plan.objective,
            success_criteria=plan.success_criteria,
            tasks=plan.tasks,
            planning_required=True,
            revision=plan.revision + 1,
        )


class FakeVerifier:
    def __init__(self, verdict: RunGoalVerification):
        self.verdict = verdict

    async def verify(self, **kwargs):
        assert kwargs["success_criteria"]
        assert kwargs["trace"]
        return self.verdict


class FakeRuntime:
    def __init__(self, *, max_steps):
        self.max_steps = max_steps

    async def run(self, **kwargs):
        del kwargs
        trace = [
            SimpleNamespace(
                capability_id="gmail.create_draft",
                arguments={"to": ["me@example.com"]},
                observation={
                    "ok": True,
                    "status": "VERIFIED",
                    "observation": {"draft_id": "draft-1"},
                },
            )
        ]
        return {
            "message": "Success: Fully Completed.",
            "execution_truth": {
                "status": "VERIFIED",
                "completed": True,
                "verified": True,
                "capability_id": "gmail.create_draft",
            },
            "trace": trace,
            "stopped": False,
            "stop_reason": "completed",
            "budget": {},
        }


@pytest.mark.asyncio
async def test_controller_overrides_false_fully_completed_claim(monkeypatch):
    monkeypatch.setattr(controller_module, "AgentRuntime", FakeRuntime)
    monkeypatch.setattr(controller_module, "find_resumable_agent_run", AsyncMock(return_value=None))
    checkpoints = AsyncMock(return_value=None)
    monkeypatch.setattr(controller_module, "checkpoint_agent_run", checkpoints)

    verifier = FakeVerifier(
        RunGoalVerification(
            False,
            missing=(
                "The XLSX schema is not proven.",
                "The Gmail draft attachment is not proven.",
            ),
        )
    )
    result = await AgentRunController(
        planner=FakePlanner(),
        verifier=verifier,
        max_replans=0,
    ).run(
        objective="Create an XLSX, PDF, and Gmail draft with the PDF attached.",
        model=object(),
        messages=[{"role": "user", "content": "run it"}],
        schemas=lambda: [],
        invoke=lambda *_args: {},
    )

    assert result["execution_truth"]["status"] == "UNVERIFIED"
    assert result["execution_truth"]["verified"] is False
    assert result["message"].startswith("Partially completed.")
    assert "XLSX schema" in result["message"]
    assert "Gmail draft attachment" in result["message"]
    final_call = checkpoints.await_args_list[-1].kwargs
    assert final_call["lifecycle_state"] == "failed"


@pytest.mark.asyncio
async def test_controller_preserves_completion_when_root_evidence_is_satisfied(monkeypatch):
    monkeypatch.setattr(controller_module, "AgentRuntime", FakeRuntime)
    monkeypatch.setattr(controller_module, "find_resumable_agent_run", AsyncMock(return_value=None))
    monkeypatch.setattr(controller_module, "checkpoint_agent_run", AsyncMock(return_value=None))

    result = await AgentRunController(
        planner=FakePlanner(),
        verifier=FakeVerifier(RunGoalVerification(True, verified=("All criteria proven.",))),
        max_replans=0,
    ).run(
        objective="Create an XLSX and verified Gmail draft.",
        model=object(),
        messages=[{"role": "user", "content": "run it"}],
        schemas=lambda: [],
        invoke=lambda *_args: {},
    )

    assert result["message"] == "Success: Fully Completed."
    assert result["execution_truth"]["status"] == "VERIFIED"
    assert result["goal_verification"]["satisfied"] is True
