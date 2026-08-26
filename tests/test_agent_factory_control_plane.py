import pytest

from packages.agents.control_plane import (
    AcceptanceContract,
    ControlPlaneValidator,
    FactoryBlueprintCompiler,
    FactoryStageRunner,
    RepairBudget,
    StageContextInjector,
    StageGraph,
    StageSpec,
    StageStatus,
    StageWorkerResult,
    ValidatorKind,
    ValidatorSpec,
)


def test_stage_graph_rejects_cycles():
    with pytest.raises(ValueError, match="dependency cycle"):
        StageGraph(
            (
                StageSpec("a", "A", dependencies=("b",)),
                StageSpec("b", "B", dependencies=("a",)),
            )
        )


def test_acceptance_contract_runs_deterministic_before_semantic():
    contract = AcceptanceContract(
        (
            ValidatorSpec(
                "semantic", "looks good", ValidatorKind.SEMANTIC, "semantic_evidence"
            ),
            ValidatorSpec(
                "provider",
                "provider confirmed",
                ValidatorKind.PROVIDER,
                "provider_verified",
            ),
            ValidatorSpec(
                "det", "count matches", ValidatorKind.DETERMINISTIC, "artifact_count"
            ),
        )
    )
    assert [item.id for item in contract.ordered()] == ["det", "provider", "semantic"]


@pytest.mark.asyncio
async def test_context_injector_is_reference_first_and_stage_scoped():
    searched = []
    materialized = []

    async def search(intent, limit):
        searched.append((intent, limit))
        return [
            {
                "ref": f"ctx:{intent}:1",
                "estimated_tokens": 20,
                "score": 0.9,
                "content": "not injected by search",
            },
            {
                "ref": f"ctx:{intent}:2",
                "estimated_tokens": 5000,
                "score": 0.1,
            },
        ]

    async def get(refs):
        materialized.extend(refs)
        return [{"ref": ref, "content": f"materialized:{ref}"} for ref in refs]

    async def capabilities(intents):
        assert list(intents) == ["read approved menu"]
        return ["files.read"]

    injector = StageContextInjector(
        search=search,
        materialize=get,
        resolve_capabilities=capabilities,
    )
    stage = StageSpec(
        "menu",
        "Read the approved menu",
        context_intents=("approved summer menu",),
        capability_intents=("read approved menu",),
    )
    capsule = await injector.build(
        stage,
        inherited_context_refs=("ctx:known",),
        artifact_refs=("artifact:menu",),
        facts={"launch_date": "Friday"},
    )

    assert capsule.stage_id == "menu"
    assert capsule.context_refs[0] == "ctx:known"
    assert "ctx:approved summer menu:1" in capsule.context_refs
    assert capsule.capability_ids == ("files.read",)
    assert capsule.artifact_refs == ("artifact:menu",)
    assert searched == [("approved summer menu", 5)]
    assert materialized
    assert all(
        item["content"].startswith("materialized:")
        for item in capsule.materialized
    )


@pytest.mark.asyncio
async def test_stage_runner_uses_fresh_capsules_and_repairs_from_defect():
    validator = ControlPlaneValidator()
    calls = []

    async def worker(stage, capsule, attempt, defect):
        calls.append(
            {
                "stage": stage.id,
                "attempt": attempt,
                "context_refs": tuple(capsule.context_refs),
                "defect": defect.failure_class if defect else None,
            }
        )
        if stage.id == "build" and attempt == 1:
            return StageWorkerResult(
                status="completed",
                strategy="method-a",
                artifacts=("pdf:bad",),
                evidence={"page_count": 397},
            )
        if stage.id == "build":
            return StageWorkerResult(
                status="completed",
                strategy="method-b",
                artifacts=("pdf:good",),
                evidence={"page_count": 400},
            )
        return StageWorkerResult(
            status="completed",
            strategy="verify-only",
            evidence={"verified": True},
        )

    async def repair(stage, defect, depth):
        assert stage.id == "build"
        assert defect.observed == 397
        assert depth == 1
        return StageSpec(
            id=stage.id,
            objective="Use an alternate PDF assembly method",
            dependencies=stage.dependencies,
            validation_ids=stage.validation_ids,
        )

    graph = StageGraph(
        (
            StageSpec("build", "Build 400-page PDF", validation_ids=("pages",)),
            StageSpec(
                "final-check",
                "Verify provider evidence",
                dependencies=("build",),
                validation_ids=("verified",),
            ),
        )
    )
    contract = AcceptanceContract(
        (
            ValidatorSpec(
                "pages",
                "PDF has 400 pages",
                ValidatorKind.DETERMINISTIC,
                "field_equals",
                expected={"value": 400},
                parameters={"path": "page_count"},
            ),
            ValidatorSpec(
                "verified",
                "Final provider result verified",
                ValidatorKind.PROVIDER,
                "provider_verified",
            ),
        )
    )
    runner = FactoryStageRunner(
        context_injector=StageContextInjector(),
        worker=worker,
        validator=validator,
        repair=repair,
        repair_budget=RepairBudget(max_attempts_per_stage=3, max_repair_depth=2),
    )
    result = await runner.run(graph=graph, acceptance=contract)

    assert result.completed is True
    assert result.waiting is False
    assert result.statuses == {
        "build": StageStatus.PASSED,
        "final-check": StageStatus.PASSED,
    }
    assert [item["stage"] for item in calls] == ["build", "build", "final-check"]
    assert calls[1]["defect"] == "validation_failed"
    assert "pdf:good" in result.artifacts


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("worker_status", "expected_stage_status", "expected_reason"),
    [
        ("waiting_approval", StageStatus.WAITING_APPROVAL, "waiting_approval"),
        ("waiting_external", StageStatus.WAITING_EXTERNAL, "waiting_external"),
    ],
)
async def test_stage_runner_pauses_waiting_stage_without_validating_or_blocking_dependents(
    worker_status,
    expected_stage_status,
    expected_reason,
):
    validator_calls = 0

    async def worker(stage, capsule, attempt, defect):
        del capsule, attempt, defect
        if stage.id == "action":
            return StageWorkerResult(
                status=worker_status,
                strategy="provider-action",
                evidence={
                    "action_id": "action-1",
                    "approval_id": "approval-1" if worker_status == "waiting_approval" else None,
                    "job_id": "job-1" if worker_status == "waiting_external" else None,
                },
            )
        raise AssertionError("dependent stage must not run while prerequisite is waiting")

    async def validator(spec, stage, result):
        del spec, stage, result
        nonlocal validator_calls
        validator_calls += 1
        return {"passed": False}

    graph = StageGraph(
        (
            StageSpec("action", "Perform action", validation_ids=("final",)),
            StageSpec("after", "Continue after action", dependencies=("action",)),
        )
    )
    result = await FactoryStageRunner(
        context_injector=StageContextInjector(),
        worker=worker,
        validator=validator,
    ).run(
        graph=graph,
        acceptance=AcceptanceContract(
            (
                ValidatorSpec(
                    "final",
                    "Action is terminal",
                    ValidatorKind.PROVIDER,
                    "provider_verified",
                ),
            )
        ),
    )

    assert validator_calls == 0
    assert result.completed is False
    assert result.waiting is True
    assert result.blocked is False
    assert result.stop_reason == expected_reason
    assert result.statuses["action"] is expected_stage_status
    assert result.statuses["after"] is StageStatus.PENDING
    assert result.defects == []


@pytest.mark.asyncio
async def test_stage_runner_stops_repeated_same_failure_and_strategy():
    validator = ControlPlaneValidator()
    attempts = 0

    async def worker(stage, capsule, attempt, defect):
        del stage, capsule, defect
        nonlocal attempts
        attempts += 1
        return StageWorkerResult(
            status="completed",
            strategy="same-method",
            evidence={"count": 3},
        )

    async def repair(stage, defect, depth):
        del defect, depth
        return stage

    graph = StageGraph(
        (StageSpec("one", "Need four items", validation_ids=("count",)),)
    )
    contract = AcceptanceContract(
        (
            ValidatorSpec(
                "count",
                "Exactly four items",
                ValidatorKind.DETERMINISTIC,
                "field_equals",
                expected={"value": 4},
                parameters={"path": "count"},
            ),
        )
    )
    result = await FactoryStageRunner(
        context_injector=StageContextInjector(),
        worker=worker,
        validator=validator,
        repair=repair,
        repair_budget=RepairBudget(
            max_attempts_per_stage=8,
            max_repair_depth=7,
            repeated_failure_threshold=2,
        ),
    ).run(graph=graph, acceptance=contract)

    assert result.completed is False
    assert result.waiting is False
    assert result.blocked is True
    assert attempts == 2
    assert result.statuses["one"] is StageStatus.BLOCKED


def test_blueprint_normalization_cannot_invent_unknown_validator_as_deterministic():
    compiler = FactoryBlueprintCompiler()
    blueprint = compiler._normalize(
        "Create the report",
        {
            "objective": {"deliverables": ["report"]},
            "validators": [
                {
                    "id": "mystery",
                    "criterion": "mystery check",
                    "kind": "deterministic",
                    "validator": "made_up_validator",
                }
            ],
            "stages": [
                {
                    "id": "build",
                    "objective": "Create report",
                    "validation_ids": ["mystery"],
                }
            ],
        },
    )
    spec = blueprint.acceptance.validators[0]
    assert spec.kind is ValidatorKind.SEMANTIC
    assert spec.validator == "semantic_evidence"
