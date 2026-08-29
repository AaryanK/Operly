import json

import pytest

from packages.agents.compaction import compact_tool_content
from packages.agents.control_plane.contracts import (
    Defect,
    StageSpec,
    StageWorkerResult,
    ValidatorKind,
    ValidatorSpec,
)
from packages.agents.control_plane.grounded_factory import (
    GroundedAgentRuntimeWorker,
    GroundedControlPlaneValidator,
    GroundedDefectRepairPlanner,
    GroundedFactoryBlueprintCompiler,
    GroundedStageContextInjector,
)
from packages.agents.control_plane.safe_factory import SafeAgentRuntimeWorker


@pytest.mark.asyncio
async def test_root_provider_operations_do_not_search_workspace_history():
    searched = []

    async def search(intent, limit):
        searched.append((intent, limit))
        return [{"ref": "workspace_message:wrong"}]

    injector = GroundedStageContextInjector(search=search)
    capsule = await injector.build(
        StageSpec(
            "mail",
            "Analyze recent email commitments",
            context_intents=(
                "Retrieve recent emails",
                "Analyze email threads for commitments",
            ),
        )
    )

    assert searched == []
    assert capsule.context_refs == ()


@pytest.mark.asyncio
async def test_explicit_workspace_history_still_searches():
    searched = []

    async def search(intent, limit):
        searched.append((intent, limit))
        return [{"ref": "context:preference", "score": 0.9}]

    injector = GroundedStageContextInjector(search=search)
    capsule = await injector.build(
        StageSpec(
            "briefing",
            "Use my prior briefing preference",
            context_intents=("Previous workspace conversation about briefing preferences",),
        )
    )

    assert searched == [("Previous workspace conversation about briefing preferences", 5)]
    assert capsule.context_refs == ("context:preference",)


@pytest.mark.asyncio
async def test_exhausted_stage_cannot_inherit_verified_status_from_last_tool(monkeypatch):
    async def exhausted(_self, stage, capsule, attempt, defect):
        del stage, capsule, attempt, defect
        return StageWorkerResult(
            status="verified",
            strategy="gmail.search -> gmail.search",
            summary=(
                "Stopped after exhausting the bounded Factory stage budget. Raw "
                "capability evidence is preserved and the control plane may repair or "
                "resume safely."
            ),
            evidence={
                "execution_truth": {
                    "status": "VERIFIED",
                    "completed": True,
                    "verified": True,
                }
            },
        )

    monkeypatch.setattr(SafeAgentRuntimeWorker, "__call__", exhausted)
    worker = GroundedAgentRuntimeWorker(
        schemas=lambda: [],
        invoke=lambda *_args: {},
        model_resolver=lambda _role: object(),
    )
    result = await worker(StageSpec("mail", "Analyze mail"), None, 1, None)

    assert result.status == "failed"
    assert result.evidence["incomplete_stage"] is True
    assert result.evidence["failure_class"] == "stage_execution_budget_exhausted"


def test_compiler_does_not_force_artifact_for_conditional_task_and_carries_ancestors():
    prompt = (
        "Check recent email and my calendar. For anything that genuinely needs action "
        "from me, create a task. Don't create a task for informational items."
    )
    payload = {
        "objective": {
            "deliverables": ["A short follow-up briefing"],
            "constraints": ["Only create genuine action items"],
            "required_side_effects": ["Create tasks when genuinely needed"],
        },
        "validators": [
            {
                "id": "tasks-created",
                "criterion": "Task handling is complete",
                "kind": "deterministic",
                "validator": "artifact_exists",
                "expected": {},
                "required": True,
            }
        ],
        "stages": [
            {
                "id": "mail",
                "objective": "Analyze recent emails",
                "dependencies": [],
                "context_intents": [],
                "capability_intents": ["email_search"],
                "validation_ids": [],
            },
            {
                "id": "calendar",
                "objective": "Cross-reference upcoming meetings",
                "dependencies": ["mail"],
                "context_intents": [],
                "capability_intents": ["calendar_read"],
                "input_refs": ["mail"],
                "validation_ids": [],
            },
            {
                "id": "tasks",
                "objective": "Create tasks for genuine action items",
                "dependencies": ["calendar"],
                "context_intents": [],
                "capability_intents": ["task_create"],
                "input_refs": ["calendar"],
                "validation_ids": ["tasks-created"],
            },
        ],
    }

    blueprint = GroundedFactoryBlueprintCompiler()._normalize(prompt, payload)
    task_stage = blueprint.graph.stage("tasks")
    validator = blueprint.acceptance.validators[0]

    assert task_stage.input_refs == ("calendar", "mail")
    assert validator.validator == "worker_status"
    assert validator.kind is ValidatorKind.DETERMINISTIC


def test_unconditional_nonartifact_mutation_uses_provider_verification():
    payload = {
        "objective": {
            "deliverables": ["Task created"],
            "required_side_effects": ["Create the task"],
        },
        "validators": [
            {
                "id": "task-created",
                "criterion": "The requested task was created",
                "kind": "deterministic",
                "validator": "artifact_exists",
                "required": True,
            }
        ],
        "stages": [
            {
                "id": "task",
                "objective": "Create the requested task",
                "capability_intents": ["task_create"],
                "validation_ids": ["task-created"],
            }
        ],
    }

    blueprint = GroundedFactoryBlueprintCompiler()._normalize(
        "Create a task for me to submit the report tomorrow.", payload
    )
    validator = blueprint.acceptance.validators[0]

    assert validator.validator == "provider_verified"
    assert validator.kind is ValidatorKind.PROVIDER


def test_provider_verified_accepts_nested_execution_truth():
    spec = ValidatorSpec(
        id="provider",
        criterion="Provider verified",
        kind=ValidatorKind.PROVIDER,
        validator="provider_verified",
    )
    result = StageWorkerResult(
        status="verified",
        evidence={
            "execution_truth": {
                "status": "VERIFIED",
                "completed": True,
                "verified": True,
            }
        },
    )

    outcome = GroundedControlPlaneValidator._provider_verified(spec, result)
    assert outcome["passed"] is True


@pytest.mark.asyncio
async def test_mutating_repair_keeps_business_facts_grounded_without_model_call():
    stage = StageSpec(
        "task",
        "Create tasks for the supplied genuine action items",
        capability_intents=("task_create",),
        input_refs=("mail", "calendar"),
    )
    defect = Defect(
        stage_id="task",
        validator_id="worker.exit_status",
        expected="successful worker result",
        observed="failed",
        retryable=True,
    )

    repaired = await GroundedDefectRepairPlanner()(stage, defect, 1)

    assert repaired is not None
    assert repaired.id == stage.id
    assert repaired.input_refs == stage.input_refs
    assert "Do not invent people" in repaired.objective
    assert "placeholder" in repaired.objective


def test_gmail_search_compaction_preserves_message_locators_and_snippets():
    raw = json.dumps(
        {
            "ok": True,
            "plugin": "gmail.search",
            "status": "VERIFIED",
            "observation": {
                "messages": [
                    {
                        "id": f"msg-{index}",
                        "thread_id": f"thread-{index}",
                        "from": f"person{index}@example.com",
                        "subject": f"Project follow-up {index}",
                        "date": "2026-08-28",
                        "snippet": "I will send the requested document tomorrow. " * 12,
                    }
                    for index in range(10)
                ]
            },
            "verification": {"success": True},
        }
    )

    compacted = compact_tool_content(raw, max_chars=1800)
    parsed = json.loads(compacted)
    messages = parsed["summary"]["observation"]["messages"]

    assert parsed["_operly_compacted"] is True
    assert messages["item_count"] == 10
    assert messages["sample"]
    assert messages["sample"][0]["id"] == "msg-0"
    assert "Project follow-up" in messages["sample"][0]["subject"]
    assert "snippet" in messages["sample"][0]
