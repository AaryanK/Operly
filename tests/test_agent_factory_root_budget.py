from types import SimpleNamespace

import pytest

from packages.agents.control_plane import (
    AgentFactoryControlPlane,
    EvidenceBoundedSemanticValidator,
    FactoryBlueprintCompiler,
)
from packages.agents.control_plane.inference_budget import FactoryInferenceBudget


class BudgetAwareCompiler:
    def __init__(self):
        self.seen_budget = None

    async def compile(self, objective, *, ingress_metadata=None, root_inference_budget=None):
        del ingress_metadata
        self.seen_budget = root_inference_budget
        return FactoryBlueprintCompiler._fallback(objective)


class LegacyCompiler:
    def __init__(self):
        self.called = False

    async def compile(self, objective, *, ingress_metadata=None):
        del ingress_metadata
        self.called = True
        return FactoryBlueprintCompiler._fallback(objective)


@pytest.mark.asyncio
async def test_root_budget_reports_only_current_resume_delta():
    budget = FactoryInferenceBudget(
        max_tokens=120_000,
        max_model_calls=48,
        initial_tokens=50_000,
        initial_model_calls=10,
    )
    initial = budget.snapshot()
    assert initial["used_tokens"] == 50_000
    assert initial["run_used_tokens"] == 0
    assert initial["model_calls"] == 10
    assert initial["run_model_calls"] == 0

    reservation = await budget.reserve(2_000)
    await budget.reconcile(reservation, 750)

    snapshot = budget.snapshot()
    assert snapshot["used_tokens"] == 50_750
    assert snapshot["run_used_tokens"] == 750
    assert snapshot["model_calls"] == 11
    assert snapshot["run_model_calls"] == 1


@pytest.mark.asyncio
async def test_control_plane_passes_root_budget_into_budget_aware_compiler():
    compiler = BudgetAwareCompiler()
    control = AgentFactoryControlPlane(
        schemas=lambda: [],
        invoke=lambda *_args: {},
        compiler=compiler,
    )
    budget = control._new_inference_budget()

    await control._compile(
        "Create the report",
        ingress_metadata={},
        root_inference_budget=budget,
    )

    assert compiler.seen_budget is budget


@pytest.mark.asyncio
async def test_control_plane_keeps_custom_legacy_compilers_compatible():
    compiler = LegacyCompiler()
    control = AgentFactoryControlPlane(
        schemas=lambda: [],
        invoke=lambda *_args: {},
        compiler=compiler,
    )

    blueprint = await control._compile(
        "Create the report",
        ingress_metadata={},
        root_inference_budget=control._new_inference_budget(),
    )

    assert compiler.called is True
    assert blueprint.graph.stages[0].objective == "Create the report"


def test_runner_shares_one_root_budget_with_worker_validator_and_repair():
    semantic = EvidenceBoundedSemanticValidator()
    control = AgentFactoryControlPlane(
        schemas=lambda: [],
        invoke=lambda *_args: {},
        semantic_validator=semantic,
    )
    budget = control._new_inference_budget()

    async def append(_event_type, _payload):
        return None

    runner = control._runner(
        run_metadata={"runtime_run_id": "run-1"},
        ledger=SimpleNamespace(append=append),
        root_inference_budget=budget,
    )

    assert runner.worker.root_inference_budget is budget
    assert runner.validator.semantic.root_inference_budget is budget
    assert runner.repair.root_inference_budget is budget
    assert runner.validator.semantic is not semantic
    assert runner.repair is not control.repair_planner
