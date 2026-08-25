import asyncio
import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from packages.software_projects.coding.contracts import (
    BaselineImport, BenchmarkTask, ExecutionState, OutcomeMetrics, RunnerJob,
)
from packages.software_projects.coding.engine import build_harness_plan, build_harness_plan_with_model
from packages.software_projects.coding.evaluation import aggregate_report, calculate_loss, compare_task
from packages.software_projects.coding.model_resolution import CapabilityResolutionError, ModelCapabilityResolver
from packages.software_projects.coding.state_machine import transition


TASKS = [
    BenchmarkTask(id="business-001", title="Mobile service dispatch", kind="small_business", split="development",
                  prompt="Build a mobile service business app with customers, manager roles, dispatch, and status updates.",
                  constraints=["tenant isolation"], acceptanceTests=["request to completion workflow"], securityTests=["cross-tenant denial"]),
    BenchmarkTask(id="science-001", title="Detector analysis workspace", kind="specialized_custom", split="held_out",
                  prompt="Build a collaborative scientific workspace where researchers upload detector data, run Python analyses, compare plots, comment, and publish approved findings.",
                  constraints=["analysis is isolated"], acceptanceTests=["upload to approved publication"], securityTests=["worker sandbox escape denied"]),
    BenchmarkTask(id="repair-001", title="Existing repository repair", kind="repository_repair", split="held_out",
                  prompt="Repair an existing repository bug while preserving its public API and all unrelated behavior.", repositoryRef="git:test-fixture",
                  constraints=["minimal patch"], acceptanceTests=["regression test passes"], securityTests=["no weakened security tests"]),
]


def metrics(value=.9, critical=False):
    return OutcomeMetrics(**{key: value for key in ("requirement","functional","build","test","security","architecture","visual","editability","traceability","regression","humanIntervention","efficiency","operability")}, criticalSecurityFailure=critical)


@pytest.mark.parametrize("task", TASKS)
def test_three_independent_task_classes_lower_to_model_independent_irs(task):
    result = build_harness_plan(task.prompt)
    assert result["requirementGraph"]["requirements"]
    assert result["capabilityGraph"]["capabilities"]
    assert len(result["architecturePlan"]["candidates"]) >= 2
    assert result["implementationPlan"]["version"] == 1
    assert all("architecturePack" not in step for step in result["implementationPlan"]["steps"])


def test_specialized_task_selects_isolated_analysis_and_realtime_boundaries():
    result = build_harness_plan(TASKS[1].prompt)
    selected = next(x for x in result["architecturePlan"]["candidates"] if x["id"] == result["architecturePlan"]["recommendedCandidateId"])
    assert selected["queue"] == "isolated worker queue"
    assert selected["realtime"] == "WebSocket collaboration service"
    assert "S3-compatible" in selected["objectStorage"]


def test_runtime_harness_uses_model_resolution_not_keyword_detection():
    class Client:
        async def chat(self, messages, tools):
            return {"content": json.dumps({
                "knownFeatureIds": ["analysis"],
                "unknownRequirements": [],
                "reason": "The request requires an isolated compute workload.",
            })}

    result = asyncio.run(build_harness_plan_with_model(
        "Create a workspace that executes researcher numerical workloads away from the control process.",
        Client(),
    ))
    assert result["knowledgeResolution"]["authority"] == "model"
    assert result["knowledgeResolution"]["knownCapabilityIds"] == ["analysis"]
    selected = next(x for x in result["architecturePlan"]["candidates"] if x["id"] == result["architecturePlan"]["recommendedCandidateId"])
    assert selected["queue"] == "isolated worker queue"


def test_runtime_harness_preserves_model_declared_unknown_requirement():
    class Client:
        async def chat(self, messages, tools):
            return {"content": json.dumps({
                "knownFeatureIds": [],
                "unknownRequirements": [{
                    "description": "Render a domain-specific spatial overlay editor",
                    "reason": "No supplied capability represents this requested behavior.",
                }],
                "reason": "The requested editor requires a new capability.",
            })}

    result = asyncio.run(build_harness_plan_with_model("Build the specialized editor.", Client()))
    assert result["knowledgeResolution"]["unknownRequirements"][0]["description"] == "Render a domain-specific spatial overlay editor"
    requirement = next(x for x in result["requirementGraph"]["requirements"] if x["source"] == "model_unmatched_requirement")
    capability = next(x for x in result["capabilityGraph"]["capabilities"] if requirement["id"] in x["requirementIds"])
    assert capability["knownImplementations"] == []


def test_runtime_harness_rejects_model_invented_capability_after_repair():
    class Client:
        def __init__(self): self.calls = 0
        async def chat(self, messages, tools):
            self.calls += 1
            return {"content": json.dumps({
                "knownFeatureIds": ["invented"],
                "unknownRequirements": [],
                "reason": "Invalid response.",
            })}

    client = Client()
    with pytest.raises(CapabilityResolutionError):
        asyncio.run(ModelCapabilityResolver(client).resolve("Build something."))
    assert client.calls == 2


def test_runner_contract_fails_closed_against_operly_execution_and_shells():
    safe = dict(id="job-1", projectId="p", planId="plan", planVersion=1, commands=[["pytest", "-q"]], cpuLimit=2, memoryMb=1024, diskMb=2048, timeoutSeconds=600)
    assert RunnerJob(**safe).executeInsideOperly is False
    with pytest.raises(ValidationError): RunnerJob(**safe, executeInsideOperly=True)
    with pytest.raises(ValidationError): RunnerJob(**{**safe, "commands": [["powershell", "-Command", "whoami"]]})
    with pytest.raises(ValidationError): RunnerJob(**{**safe, "productionDeploymentAllowed": True})


def test_iterative_state_machine_repairs_and_rejects_shortcuts():
    state = ExecutionState.building
    for target in (ExecutionState.diagnosing, ExecutionState.repairing, ExecutionState.building, ExecutionState.testing,
                   ExecutionState.running, ExecutionState.inspecting, ExecutionState.acceptance_passed):
        state = transition(state, target)
    assert state is ExecutionState.acceptance_passed
    with pytest.raises(ValueError): transition(ExecutionState.implementing, ExecutionState.acceptance_passed)


def test_loss_is_deterministic_and_critical_security_invalidates_result():
    assert calculate_loss(metrics(.8)) == calculate_loss(metrics(.8))
    assert calculate_loss(metrics(.99, critical=True)) == 1.0


def test_comparison_keeps_held_out_separate_and_requires_evidence_before_parity():
    task = TASKS[1]
    baseline = BaselineImport(taskId=task.id, agentVersion="fixture", independentRunId="codex-1", sourceRevision="abc", metrics=metrics(.8), evidenceRefs=["artifact://codex"], recordedAt=datetime.now(timezone.utc))
    report = compare_task(task, metrics(.9), baseline, ["artifact://operly"])
    aggregate = aggregate_report([report])
    assert aggregate.development["count"] == 0
    assert aggregate.heldOut["count"] == 1
    assert aggregate.parityClaimAllowed is True
    assert aggregate_report([]).parityClaimAllowed is False
