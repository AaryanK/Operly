"""High-level composition root for the Operly agent factory control plane."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .compiler import FactoryBlueprintCompiler
from .context_injector import (
    CapabilityResolver,
    ContextMaterialize,
    ContextSearch,
    StageContextInjector,
)
from .contracts import RepairBudget
from .evidence import FactoryEvidenceLedger
from .repair import DefectRepairPlanner
from .stage_runner import FactoryExecutionResult, FactoryStageRunner
from .validation import ControlPlaneValidator, PythonTestExecutor, SemanticValidator
from .worker_adapter import AgentRuntimeWorker, CapabilityInvoker, SchemaLoader


@dataclass(frozen=True, slots=True)
class FactoryRunResponse:
    runtime_run_id: str
    message: str
    execution: FactoryExecutionResult
    blueprint: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        if self.execution.completed:
            truth_status = "VERIFIED"
        elif self.execution.stop_reason == "waiting_approval":
            truth_status = "WAITING_APPROVAL"
        elif self.execution.stop_reason == "waiting_external":
            truth_status = "PENDING_EVIDENCE"
        elif self.execution.blocked:
            truth_status = "BLOCKED"
        else:
            truth_status = "FAILED"
        return {
            "runtime_run_id": self.runtime_run_id,
            "message": self.message,
            "execution_truth": {
                "status": truth_status,
                "completed": self.execution.completed,
                "verified": self.execution.completed,
                "pending": self.execution.waiting,
            },
            "factory": self.execution.as_dict(),
            "blueprint": self.blueprint,
            # Waiting is a durable pause, not a stopped/failed job.
            "stopped": not self.execution.completed and not self.execution.waiting,
            "stop_reason": self.execution.stop_reason,
        }


class AgentFactoryControlPlane:
    """Compile success first, distribute minimum context, then run disposable workers.

    All authority remains in the supplied schema/invocation/context callbacks. This
    class orchestrates them; it does not resolve a principal or widen capability scope.
    """

    def __init__(
        self,
        *,
        schemas: SchemaLoader,
        invoke: CapabilityInvoker,
        context_search: ContextSearch | None = None,
        context_materialize: ContextMaterialize | None = None,
        capability_resolver: CapabilityResolver | None = None,
        python_validator: PythonTestExecutor | None = None,
        semantic_validator: SemanticValidator | None = None,
        model_resolver=None,
        compiler: FactoryBlueprintCompiler | None = None,
        repair_planner: DefectRepairPlanner | None = None,
        repair_budget: RepairBudget | None = None,
        max_worker_steps: int = 8,
        max_parallelism: int = 4,
    ) -> None:
        self.schemas = schemas
        self.invoke = invoke
        self.context_search = context_search
        self.context_materialize = context_materialize
        self.capability_resolver = capability_resolver
        self.python_validator = python_validator
        self.semantic_validator = semantic_validator
        self.model_resolver = model_resolver
        self.compiler = compiler or FactoryBlueprintCompiler()
        self.repair_planner = repair_planner or DefectRepairPlanner()
        self.repair_budget = (repair_budget or RepairBudget()).normalized()
        self.max_worker_steps = max_worker_steps
        self.max_parallelism = max_parallelism

    @staticmethod
    def _message(execution: FactoryExecutionResult) -> str:
        if execution.completed:
            passed = [
                attempt.result.summary.strip()
                for attempt in execution.attempts
                if not attempt.defects and attempt.result.summary.strip()
            ]
            return passed[-1][:24_000] if passed else "Completed and verified."
        if execution.stop_reason == "waiting_approval":
            return "Approval is required before the current stage can continue."
        if execution.stop_reason == "waiting_external":
            return (
                "Work has been accepted and is still in progress. Operly is waiting "
                "for terminal completion evidence before continuing the remaining stages."
            )
        if execution.defects:
            last = execution.defects[-1]
            return (
                "Operly could not verify the full objective. "
                f"Stage {last.stage_id} failed {last.validator_id}: expected "
                f"{last.expected!r}, observed {last.observed!r}."
            )[:24_000]
        return (
            "Operly stopped before the objective was verified "
            f"({execution.stop_reason})."
        )

    async def run(
        self,
        *,
        objective: str,
        metadata: dict[str, Any],
        ingress_metadata: dict[str, Any] | None = None,
        initial_context_refs: set[str] | None = None,
        initial_artifact_refs: set[str] | None = None,
        facts: dict[str, Any] | None = None,
    ) -> FactoryRunResponse:
        runtime_run_id = str(metadata.get("runtime_run_id") or uuid4())
        run_metadata = {
            **dict(metadata),
            "runtime_run_id": runtime_run_id,
            "runtime_controller": "factory",
        }
        blueprint = await self.compiler.compile(
            objective,
            ingress_metadata={
                **dict(ingress_metadata or {}),
                "channel": metadata.get("channel"),
                "surface": metadata.get("surface"),
            },
        )
        ledger = FactoryEvidenceLedger(
            runtime_run_id=runtime_run_id,
            objective=objective,
            metadata=run_metadata,
        )
        await ledger.start(blueprint)

        injector = StageContextInjector(
            search=self.context_search,
            materialize=self.context_materialize,
            resolve_capabilities=self.capability_resolver,
        )
        worker = AgentRuntimeWorker(
            schemas=self.schemas,
            invoke=self.invoke,
            model_resolver=self.model_resolver,
            max_steps=self.max_worker_steps,
            inference_metadata=run_metadata,
        )
        validator = ControlPlaneValidator(
            python_test=self.python_validator,
            semantic=self.semantic_validator,
        )
        runner = FactoryStageRunner(
            context_injector=injector,
            worker=worker,
            validator=validator,
            repair=self.repair_planner,
            event_sink=ledger.append,
            repair_budget=self.repair_budget,
            max_parallelism=self.max_parallelism,
        )
        execution = await runner.run(
            graph=blueprint.graph,
            acceptance=blueprint.acceptance,
            initial_context_refs=initial_context_refs,
            initial_artifact_refs=initial_artifact_refs,
            facts=facts,
        )
        await ledger.finish(execution)
        return FactoryRunResponse(
            runtime_run_id=runtime_run_id,
            message=self._message(execution),
            execution=execution,
            blueprint=blueprint.as_dict(),
        )
