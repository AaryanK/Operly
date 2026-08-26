import pytest

from packages.agents.control_plane import (
    AcceptanceContract,
    ControlPlaneValidator,
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


@pytest.mark.asyncio
async def test_failed_attempt_artifacts_are_audit_only_and_not_reinjected():
    capsules = []
    events = []

    async def worker(stage, capsule, attempt, defect):
        del defect
        capsules.append((stage.id, attempt, set(capsule.artifact_refs)))
        if stage.id == "build" and attempt == 1:
            return StageWorkerResult(
                status="completed",
                strategy="bad-method",
                artifacts=("artifact:bad",),
                evidence={"count": 3},
            )
        if stage.id == "build":
            return StageWorkerResult(
                status="completed",
                strategy="good-method",
                artifacts=("artifact:good",),
                evidence={"count": 4},
            )
        assert "artifact:good" in set(capsule.artifact_refs)
        assert "artifact:bad" not in set(capsule.artifact_refs)
        return StageWorkerResult(status="completed", strategy="consume-good")

    async def repair(stage, defect, depth):
        del defect, depth
        return StageSpec(
            id=stage.id,
            objective="Use a different build method",
            dependencies=stage.dependencies,
            validation_ids=stage.validation_ids,
        )

    async def event_sink(event_type, payload):
        events.append((event_type, dict(payload)))

    graph = StageGraph(
        (
            StageSpec("build", "Build four items", validation_ids=("count",)),
            StageSpec("consume", "Consume build output", dependencies=("build",)),
        )
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
        validator=ControlPlaneValidator(),
        repair=repair,
        event_sink=event_sink,
        repair_budget=RepairBudget(max_attempts_per_stage=3, max_repair_depth=2),
    ).run(
        graph=graph,
        acceptance=contract,
        initial_artifact_refs={"artifact:root"},
    )

    assert result.completed is True
    assert ("build", 2, {"artifact:root"}) in capsules
    assert "artifact:bad" not in result.artifacts
    assert result.stage_artifacts["build"] == {"artifact:good"}
    assert result.artifacts == {"artifact:root", "artifact:good"}
    assert any(
        event_type == "stage.attempted"
        and payload.get("artifact_refs") == ["artifact:bad"]
        for event_type, payload in events
    )


@pytest.mark.asyncio
async def test_downstream_stage_receives_only_dependency_outputs_not_sibling_outputs():
    seen = {}

    async def worker(stage, capsule, attempt, defect):
        del attempt, defect
        seen[stage.id] = set(capsule.artifact_refs)
        if stage.id == "left":
            return StageWorkerResult(status="completed", artifacts=("artifact:left",))
        if stage.id == "right":
            return StageWorkerResult(status="completed", artifacts=("artifact:right",))
        return StageWorkerResult(status="completed")

    graph = StageGraph(
        (
            StageSpec("left", "Left branch", can_parallelize=True),
            StageSpec("right", "Right branch", can_parallelize=True),
            StageSpec("left-child", "Use left", dependencies=("left",)),
        )
    )
    result = await FactoryStageRunner(
        context_injector=StageContextInjector(),
        worker=worker,
        validator=ControlPlaneValidator(),
        max_parallelism=4,
    ).run(graph=graph, acceptance=AcceptanceContract(()))

    assert result.completed is True
    assert "artifact:left" in seen["left-child"]
    assert "artifact:right" not in seen["left-child"]


@pytest.mark.asyncio
async def test_waiting_stage_resumes_from_terminal_evidence_without_repeating_side_effect():
    initial_calls = []

    async def initial_worker(stage, capsule, attempt, defect):
        del capsule, attempt, defect
        initial_calls.append(stage.id)
        if stage.id != "action":
            raise AssertionError("dependent stage must not run before completion evidence")
        return StageWorkerResult(
            status="waiting_external",
            strategy="submit-once",
            artifacts=("artifact:pending",),
            evidence={"job_id": "job-1", "deferred": True},
        )

    graph = StageGraph(
        (
            StageSpec("action", "Submit durable action", validation_ids=("verified",)),
            StageSpec("after", "Use terminal output", dependencies=("action",)),
        )
    )
    acceptance = AcceptanceContract(
        (
            ValidatorSpec(
                "verified",
                "Provider confirms terminal completion",
                ValidatorKind.PROVIDER,
                "provider_verified",
            ),
        )
    )
    first = await FactoryStageRunner(
        context_injector=StageContextInjector(),
        worker=initial_worker,
        validator=ControlPlaneValidator(),
    ).run(graph=graph, acceptance=acceptance)

    assert first.waiting is True
    assert first.statuses["action"] is StageStatus.WAITING_EXTERNAL
    assert "artifact:pending" not in first.artifacts

    resumed_calls = []

    async def resumed_worker(stage, capsule, attempt, defect):
        del attempt, defect
        resumed_calls.append(stage.id)
        if stage.id == "action":
            raise AssertionError("resuming terminal evidence must not repeat the action")
        assert "artifact:final" in set(capsule.artifact_refs)
        return StageWorkerResult(status="completed", strategy="consume-terminal-output")

    second = await FactoryStageRunner(
        context_injector=StageContextInjector(),
        worker=resumed_worker,
        validator=ControlPlaneValidator(),
    ).run(
        graph=graph,
        acceptance=acceptance,
        resume_statuses=first.statuses,
        prior_stage_artifacts=first.stage_artifacts,
        prior_stage_evidence_refs=first.stage_evidence_refs,
        resume_results={
            "action": StageWorkerResult(
                status="completed",
                strategy="provider-terminal-evidence",
                artifacts=("artifact:final",),
                evidence={"verified": True},
                evidence_refs=("evidence:job-1-terminal",),
            )
        },
    )

    assert second.completed is True
    assert resumed_calls == ["after"]
    assert second.attempts[0].stage_id == "action"
    assert second.attempts[0].source == "resume"
    assert second.stage_artifacts["action"] == {"artifact:final"}
    assert "artifact:pending" not in second.artifacts


@pytest.mark.asyncio
async def test_resume_result_cannot_skip_a_nonwaiting_stage():
    graph = StageGraph((StageSpec("one", "Do one thing"),))

    async def worker(stage, capsule, attempt, defect):
        del stage, capsule, attempt, defect
        return StageWorkerResult(status="completed")

    with pytest.raises(ValueError, match="waiting stage"):
        await FactoryStageRunner(
            context_injector=StageContextInjector(),
            worker=worker,
            validator=ControlPlaneValidator(),
        ).run(
            graph=graph,
            acceptance=AcceptanceContract(()),
            resume_statuses={"one": StageStatus.PENDING},
            resume_results={"one": StageWorkerResult(status="completed")},
        )
