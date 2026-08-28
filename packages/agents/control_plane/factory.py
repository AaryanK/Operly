"""High-level composition root for the Operly agent factory control plane."""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Iterable
from uuid import uuid4

from packages.agents.persistence import load_agent_run

from .compiler import FactoryBlueprint, FactoryBlueprintCompiler
from .context_injector import (
    CapabilityResolver,
    ContextMaterialize,
    ContextSearch,
    StageContextInjector,
)
from .contracts import (
    AcceptanceContract,
    ObjectiveSpec,
    RepairBudget,
    StageGraph,
    StageSpec,
    StageWorkerResult,
    ValidatorKind,
    ValidatorSpec,
)
from .evidence import FactoryEvidenceLedger
from .inference_budget import FactoryInferenceBudget
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
            "stopped": not self.execution.completed and not self.execution.waiting,
            "stop_reason": self.execution.stop_reason,
        }


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _blueprint_from_checkpoint(checkpoint: dict[str, Any]) -> FactoryBlueprint:
    """Rebuild frozen contracts from persisted factory state without model inference."""

    factory = checkpoint.get("factory")
    if not isinstance(factory, dict):
        raise ValueError("Factory checkpoint is missing control-plane state")

    objective_data = factory.get("objective_spec")
    acceptance_data = factory.get("acceptance")
    graph_data = factory.get("graph")
    if not isinstance(objective_data, dict):
        raise ValueError("Factory checkpoint is missing objective_spec")
    if not isinstance(acceptance_data, dict):
        raise ValueError("Factory checkpoint is missing acceptance contract")
    if not isinstance(graph_data, dict):
        raise ValueError("Factory checkpoint is missing stage graph")

    objective_text = str(
        objective_data.get("objective")
        or checkpoint.get("objective")
        or ""
    ).strip()
    if not objective_text:
        raise ValueError("Factory checkpoint objective is empty")
    objective = ObjectiveSpec(
        objective=objective_text,
        deliverables=_strings(objective_data.get("deliverables")),
        constraints=_strings(objective_data.get("constraints")),
        required_side_effects=_strings(objective_data.get("required_side_effects")),
    )

    validators: list[ValidatorSpec] = []
    for raw in acceptance_data.get("validators") or ():
        if not isinstance(raw, dict):
            continue
        try:
            kind = ValidatorKind(str(raw.get("kind") or "deterministic"))
        except ValueError as exc:
            raise ValueError("Factory checkpoint contains an invalid validator kind") from exc
        validator_id = str(raw.get("id") or "").strip()
        criterion = str(raw.get("criterion") or "").strip()
        if not validator_id or not criterion:
            raise ValueError("Factory checkpoint contains an invalid validator")
        validators.append(
            ValidatorSpec(
                id=validator_id,
                criterion=criterion,
                kind=kind,
                validator=str(raw.get("validator") or "evidence_present").strip(),
                expected=(
                    dict(raw.get("expected"))
                    if isinstance(raw.get("expected"), dict)
                    else {}
                ),
                parameters=(
                    dict(raw.get("parameters"))
                    if isinstance(raw.get("parameters"), dict)
                    else {}
                ),
                required=bool(raw.get("required", True)),
            )
        )
    acceptance = AcceptanceContract(tuple(validators))

    stages: list[StageSpec] = []
    for raw in graph_data.get("stages") or ():
        if not isinstance(raw, dict):
            continue
        stages.append(
            StageSpec(
                id=str(raw.get("id") or "").strip(),
                objective=str(raw.get("objective") or "").strip(),
                dependencies=_strings(raw.get("dependencies")),
                context_intents=_strings(raw.get("context_intents")),
                capability_intents=_strings(raw.get("capability_intents")),
                input_refs=_strings(raw.get("input_refs")),
                validation_ids=_strings(raw.get("validation_ids")),
                assigned_role=str(raw.get("assigned_role") or "business_agent"),
                can_parallelize=bool(raw.get("can_parallelize")),
                max_output_chars=max(
                    1000,
                    min(int(raw.get("max_output_chars") or 12_000), 100_000),
                ),
            )
        )
    return FactoryBlueprint(
        objective=objective,
        acceptance=acceptance,
        graph=StageGraph(tuple(stages)),
    )


class AgentFactoryControlPlane:
    """Compile success first, distribute minimum context, then run disposable workers.

    All authority remains in the supplied schema/invocation/context callbacks. This
    class orchestrates them; it does not resolve a principal or widen capability scope.
    Every standard model inference for one objective debits one root budget that is
    persisted and restored when approval/external work resumes.
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
        max_model_calls: int = 48,
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
        self.max_model_calls = max(1, min(int(max_model_calls), 500))

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

    def _new_inference_budget(
        self,
        *,
        initial_tokens: int = 0,
        initial_model_calls: int = 0,
    ) -> FactoryInferenceBudget:
        return FactoryInferenceBudget(
            max_tokens=self.repair_budget.max_tokens,
            max_model_calls=self.max_model_calls,
            initial_tokens=initial_tokens,
            initial_model_calls=initial_model_calls,
        )

    async def _compile(
        self,
        objective: str,
        *,
        ingress_metadata: dict[str, Any],
        root_inference_budget: FactoryInferenceBudget,
    ) -> FactoryBlueprint:
        """Pass the root ledger to budget-aware compilers without breaking custom ones."""
        compile_fn = self.compiler.compile
        try:
            parameters = inspect.signature(compile_fn).parameters.values()
            accepts_budget = any(
                parameter.name == "root_inference_budget"
                or parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            )
        except (TypeError, ValueError):
            accepts_budget = False
        if accepts_budget:
            return await compile_fn(
                objective,
                ingress_metadata=ingress_metadata,
                root_inference_budget=root_inference_budget,
            )
        return await compile_fn(
            objective,
            ingress_metadata=ingress_metadata,
        )

    @staticmethod
    def _bind_budget(component, budget: FactoryInferenceBudget):
        binder = getattr(component, "with_root_inference_budget", None)
        if callable(binder):
            return binder(budget)
        return component

    def _runner(
        self,
        *,
        run_metadata: dict[str, Any],
        ledger: FactoryEvidenceLedger,
        root_inference_budget: FactoryInferenceBudget,
    ) -> FactoryStageRunner:
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
            root_inference_budget=root_inference_budget,
        )
        semantic_validator = self._bind_budget(
            self.semantic_validator,
            root_inference_budget,
        )
        repair_planner = self._bind_budget(
            self.repair_planner,
            root_inference_budget,
        )
        validator = ControlPlaneValidator(
            python_test=self.python_validator,
            semantic=semantic_validator,
        )
        return FactoryStageRunner(
            context_injector=injector,
            worker=worker,
            validator=validator,
            repair=repair_planner,
            event_sink=ledger.append,
            repair_budget=self.repair_budget,
            max_parallelism=self.max_parallelism,
        )

    @staticmethod
    def _response(
        runtime_run_id: str,
        blueprint: FactoryBlueprint,
        execution: FactoryExecutionResult,
    ) -> FactoryRunResponse:
        return FactoryRunResponse(
            runtime_run_id=runtime_run_id,
            message=AgentFactoryControlPlane._message(execution),
            execution=execution,
            blueprint=blueprint.as_dict(),
        )

    async def run(
        self,
        *,
        objective: str,
        metadata: dict[str, Any],
        ingress_metadata: dict[str, Any] | None = None,
        initial_context_refs: set[str] | None = None,
        initial_artifact_refs: set[str] | None = None,
        stage_input_artifact_refs: dict[str, Iterable[str]] | None = None,
        facts: dict[str, Any] | None = None,
    ) -> FactoryRunResponse:
        runtime_run_id = str(metadata.get("runtime_run_id") or uuid4())
        run_metadata = {
            **dict(metadata),
            "runtime_run_id": runtime_run_id,
            "runtime_controller": "factory",
        }
        root_inference_budget = self._new_inference_budget()
        blueprint = await self._compile(
            objective,
            ingress_metadata={
                **dict(ingress_metadata or {}),
                "channel": metadata.get("channel"),
                "surface": metadata.get("surface"),
            },
            root_inference_budget=root_inference_budget,
        )
        ledger = FactoryEvidenceLedger(
            runtime_run_id=runtime_run_id,
            objective=objective,
            metadata=run_metadata,
        )
        await ledger.start(
            blueprint,
            initial_context_refs=initial_context_refs,
            initial_artifact_refs=initial_artifact_refs,
            stage_input_artifact_refs=stage_input_artifact_refs,
        )
        execution = await self._runner(
            run_metadata=run_metadata,
            ledger=ledger,
            root_inference_budget=root_inference_budget,
        ).run(
            graph=blueprint.graph,
            acceptance=blueprint.acceptance,
            initial_context_refs=initial_context_refs,
            initial_artifact_refs=initial_artifact_refs,
            stage_input_artifact_refs=stage_input_artifact_refs,
            facts=facts,
        )
        inference_snapshot = root_inference_budget.snapshot()
        # StageRunner retains per-attempt usage for repair decisions. The root result is
        # the objective-wide delta, including compiler/semantic/repair model calls.
        execution.token_usage = int(inference_snapshot.get("run_used_tokens") or 0)
        await ledger.finish(execution, inference_snapshot=inference_snapshot)
        return self._response(runtime_run_id, blueprint, execution)

    async def resume(
        self,
        *,
        runtime_run_id: str,
        metadata: dict[str, Any],
        stage_result: StageWorkerResult,
        stage_id: str | None = None,
        facts: dict[str, Any] | None = None,
    ) -> FactoryRunResponse:
        """Resume the exact waiting station from terminal provider/approval evidence.

        The frozen objective/acceptance/DAG are loaded from the durable checkpoint.
        The waiting side effect is never re-issued by a worker; ``stage_result`` is
        validated as the terminal result for that station, then downstream work may
        continue under freshly resolved authority/context. Inference allowance resumes
        from the persisted root usage instead of resetting after an approval boundary.
        """

        clean_run_id = str(runtime_run_id or "").strip()
        if not clean_run_id:
            raise ValueError("runtime_run_id is required")
        run_metadata = {
            **dict(metadata),
            "runtime_run_id": clean_run_id,
            "runtime_controller": "factory",
        }
        loaded = await load_agent_run(clean_run_id, metadata=run_metadata)
        if loaded is None:
            raise LookupError("Factory run was not found in the current execution scope")
        checkpoint = loaded.get("checkpoint")
        if not isinstance(checkpoint, dict):
            raise ValueError("Factory run has no resumable checkpoint")
        blueprint = _blueprint_from_checkpoint(checkpoint)
        factory_state = checkpoint.get("factory")
        if not isinstance(factory_state, dict):
            raise ValueError("Factory run has no resumable state")

        statuses = (
            dict(factory_state.get("statuses"))
            if isinstance(factory_state.get("statuses"), dict)
            else {}
        )
        waiting_stages = (
            dict(factory_state.get("waiting_stages"))
            if isinstance(factory_state.get("waiting_stages"), dict)
            else {}
        )
        selected_stage = str(stage_id or "").strip()
        if not selected_stage:
            if len(waiting_stages) != 1:
                raise ValueError(
                    "stage_id is required when a Factory run has multiple/no waiting stages"
                )
            selected_stage = next(iter(waiting_stages))
        waiting_status = str(statuses.get(selected_stage) or "")
        if waiting_status not in {"waiting_approval", "waiting_external"}:
            raise ValueError("Factory resume target is not currently waiting")

        initial_context_refs = set(_strings(factory_state.get("initial_context_refs")))
        initial_artifact_refs = set(_strings(factory_state.get("initial_artifact_refs")))
        stage_input_artifact_refs = {
            str(key): tuple(value)
            for key, value in dict(
                factory_state.get("stage_input_artifact_refs") or {}
            ).items()
            if isinstance(value, (list, tuple, set))
        }
        prior_stage_artifacts = {
            str(key): tuple(value)
            for key, value in dict(factory_state.get("stage_artifacts") or {}).items()
            if isinstance(value, (list, tuple, set))
        }
        prior_stage_evidence_refs = {
            str(key): tuple(value)
            for key, value in dict(
                factory_state.get("stage_evidence_refs") or {}
            ).items()
            if isinstance(value, (list, tuple, set))
        }
        prior_tokens = max(0, int(factory_state.get("token_usage") or 0))
        prior_model_calls = max(0, int(factory_state.get("model_calls") or 0))
        root_inference_budget = self._new_inference_budget(
            initial_tokens=prior_tokens,
            initial_model_calls=prior_model_calls,
        )

        ledger = FactoryEvidenceLedger(
            runtime_run_id=clean_run_id,
            objective=blueprint.objective.objective,
            metadata=run_metadata,
            initial_projection=checkpoint,
        )
        await ledger.resume(blueprint, stage_id=selected_stage)
        execution = await self._runner(
            run_metadata=run_metadata,
            ledger=ledger,
            root_inference_budget=root_inference_budget,
        ).run(
            graph=blueprint.graph,
            acceptance=blueprint.acceptance,
            initial_context_refs=initial_context_refs,
            initial_artifact_refs=initial_artifact_refs,
            stage_input_artifact_refs=stage_input_artifact_refs,
            facts=facts,
            resume_statuses=statuses,
            prior_stage_artifacts=prior_stage_artifacts,
            prior_stage_evidence_refs=prior_stage_evidence_refs,
            resume_results={selected_stage: stage_result},
        )
        inference_snapshot = root_inference_budget.snapshot()
        execution.token_usage = int(inference_snapshot.get("run_used_tokens") or 0)
        await ledger.finish(execution, inference_snapshot=inference_snapshot)
        return self._response(clean_run_id, blueprint, execution)
