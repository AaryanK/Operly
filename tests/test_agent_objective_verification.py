from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import packages.agents.controller as controller_module
from packages.agents.controller import AgentRunController
from packages.agents.run_state import CompactRunState, RunPlan, RunTask
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


class NoReplanPlanner(FakePlanner):
    async def replan(self, state, *, reason, trace_metadata=None):
        del state, reason, trace_metadata
        raise AssertionError("pending deferred work must not be replanned")


class FakeVerifier:
    def __init__(self, verdict: RunGoalVerification):
        self.verdict = verdict

    async def verify(self, **kwargs):
        assert kwargs["success_criteria"]
        assert kwargs["trace"]
        return self.verdict


class UnexpectedVerifier:
    async def verify(self, **kwargs):
        del kwargs
        raise AssertionError("pending deferred work must not be verified as terminal")


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


class DeferredSoftwareRuntime:
    def __init__(self, *, max_steps):
        self.max_steps = max_steps

    async def run(self, **kwargs):
        observation = {
            "ok": True,
            "status": "VERIFIED",
            "observation": {
                "project_id": "project-1",
                "solution_id": "solution-1",
                "job_id": "job-1",
                "job_accepted": True,
                "build_state": "queued",
                "build_success": False,
                "deferred": True,
                "continuation_kind": "software_build",
            },
        }
        hook = kwargs.get("on_observation")
        assert hook is not None
        await hook(
            "software.build",
            {"objective": "Build a QR clock-in application"},
            observation,
        )
        return {
            "message": "All business rules are implemented and RBAC is enforced.",
            "execution_truth": {
                "status": "VERIFIED",
                "completed": True,
                "verified": True,
                "capability_id": "software.build",
            },
            "trace": [
                SimpleNamespace(
                    capability_id="software.build",
                    arguments={"objective": "Build a QR clock-in application"},
                    observation=observation,
                )
            ],
            "stopped": False,
            "stop_reason": "completed",
            "budget": {},
        }


class UnexpectedRuntime:
    def __init__(self, *, max_steps):
        self.max_steps = max_steps

    async def run(self, **kwargs):
        del kwargs
        raise AssertionError("a waiting external run must not execute the agent micro-loop again")


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


@pytest.mark.asyncio
async def test_deferred_software_build_waits_for_evidence_instead_of_replanning(monkeypatch):
    monkeypatch.setattr(controller_module, "AgentRuntime", DeferredSoftwareRuntime)
    monkeypatch.setattr(controller_module, "find_resumable_agent_run", AsyncMock(return_value=None))
    checkpoints = AsyncMock(return_value=None)
    monkeypatch.setattr(controller_module, "checkpoint_agent_run", checkpoints)

    result = await AgentRunController(
        planner=NoReplanPlanner(),
        verifier=UnexpectedVerifier(),
        max_replans=1,
    ).run(
        objective="Build a QR clock-in and clock-out application.",
        model=object(),
        messages=[{"role": "user", "content": "build it"}],
        schemas=lambda: [],
        invoke=lambda *_args: {},
    )

    assert result["execution_truth"]["status"] == "PENDING_EVIDENCE"
    assert result["execution_truth"]["pending"] is True
    assert result["execution_truth"]["verified"] is False
    assert result["execution_truth"]["project_id"] == "project-1"
    assert result["execution_truth"]["job_id"] == "job-1"
    assert result["objective_status"] == "pending_evidence"
    assert result["stop_reason"] == "waiting_external_completion"
    assert result["replans"] == 0
    assert "still in progress" in result["message"]
    assert "implemented" not in result["message"].lower()
    assert result["run_state"]["facts"]["deferred_work"]["state"] == "waiting"
    assert result["run_state"]["facts"]["deferred_work"]["capability_id"] == "software.build"
    final_call = checkpoints.await_args_list[-1].kwargs
    assert final_call["lifecycle_state"] == "waiting_external"
    assert final_call["payload"]["objective_status"] == "pending_evidence"


@pytest.mark.asyncio
async def test_waiting_external_run_returns_pending_without_rerunning_agent(monkeypatch):
    state = CompactRunState(
        objective="Build a QR clock-in and clock-out application.",
        plan=RunPlan(
            objective="Build a QR clock-in and clock-out application.",
            success_criteria=("Produce verified runnable software.",),
            tasks=[RunTask(id="task-1", objective="Build it")],
            planning_required=True,
        ),
        facts={
            "deferred_work": {
                "capability_id": "software.build",
                "continuation_kind": "software_build",
                "job_id": "job-1",
                "project_id": "project-1",
                "solution_id": "solution-1",
                "state": "waiting",
            }
        },
    )
    existing = {
        "run_id": "run-1",
        "state": "waiting_external",
        "objective": state.objective,
        "checkpoint": state.as_dict(),
        "completed_at": None,
    }
    monkeypatch.setattr(controller_module, "AgentRuntime", UnexpectedRuntime)
    monkeypatch.setattr(
        controller_module,
        "find_resumable_agent_run",
        AsyncMock(return_value=existing),
    )
    checkpoints = AsyncMock(return_value=None)
    monkeypatch.setattr(controller_module, "checkpoint_agent_run", checkpoints)

    result = await AgentRunController(
        planner=NoReplanPlanner(),
        verifier=UnexpectedVerifier(),
    ).run(
        objective=state.objective,
        model=object(),
        messages=[{"role": "user", "content": "build it"}],
        schemas=lambda: [],
        invoke=lambda *_args: {},
        inference_metadata={"_conversation_id": "conversation-1"},
    )

    assert result["runtime_run_id"] == "run-1"
    assert result["resumed"] is True
    assert result["execution_truth"]["status"] == "PENDING_EVIDENCE"
    assert result["objective_status"] == "pending_evidence"
    assert result["replans"] == 0
    checkpoints.assert_not_awaited()
