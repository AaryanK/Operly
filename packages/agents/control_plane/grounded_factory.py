"""Grounded Factory safety layer for evidence-preserving recovery.

This layer keeps operational data on direct capability paths, prevents incomplete
bounded stages from masquerading as verified, and ensures mutating repair attempts
remain grounded in validated upstream results.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from .compiler import FactoryBlueprint, FactoryBlueprintCompiler
from .contracts import (
    AcceptanceContract,
    Defect,
    StageGraph,
    StageSpec,
    StageWorkerResult,
    ValidatorKind,
    ValidatorSpec,
)
from .repair import DefectRepairPlanner
from .runtime_aware_factory import RuntimeAwareAgentFactoryControlPlane
from .safe_factory import (
    SafeAgentRuntimeWorker,
    SafeFactoryStageRunner,
    SafeStageContextInjector,
)
from .validation import ControlPlaneValidator


_EXPLICIT_HISTORY_MARKERS = (
    "history",
    "historical",
    "previous",
    "prior",
    "earlier",
    "conversation",
    "memory",
    "remember",
    "workspace message",
    "workspace messages",
    "workspace data",
    "workspace context",
    "channel message",
    "channel messages",
    "attachment",
    "attached",
    "uploaded",
    "artifact",
    "document",
    "file",
    "record",
    "policy",
    "preference",
    "approved",
)
_MUTATION_WORDS = frozenset(
    {
        "create",
        "add",
        "send",
        "update",
        "edit",
        "modify",
        "delete",
        "remove",
        "cancel",
        "complete",
        "close",
        "publish",
        "archive",
        "move",
        "rename",
        "adjust",
        "set",
        "invite",
        "draft",
    }
)
_ARTIFACT_WORDS = frozenset(
    {
        "artifact",
        "artifacts",
        "file",
        "files",
        "pdf",
        "document",
        "documents",
        "spreadsheet",
        "spreadsheets",
        "excel",
        "workbook",
    }
)
_CONDITIONAL_SIDE_EFFECT_MARKERS = (
    " if ",
    "if there",
    "if any",
    "for anything",
    "for any ",
    "only if",
    "only when",
    "when needed",
    "when necessary",
    "genuinely need",
    "genuinely needs",
    "don't create",
    "do not create",
    "not every",
)


def _words(value: str) -> set[str]:
    import re

    return {
        token
        for token in re.split(r"[^a-z0-9]+", str(value or "").lower())
        if token
    }


def _explicit_history_intents(stage: StageSpec) -> tuple[str, ...]:
    """Return only intents that explicitly request ambient retained history.

    Operational phrases such as "Retrieve recent emails" belong to Gmail, not
    workspace semantic history. This rule applies equally to root and dependent stages.
    """

    output: list[str] = []
    for raw in stage.context_intents:
        clean = " ".join(str(raw or "").split()).strip()
        lowered = clean.lower()
        if clean and any(marker in lowered for marker in _EXPLICIT_HISTORY_MARKERS):
            output.append(clean)
    return tuple(output)


def _stage_is_mutating(stage: StageSpec) -> bool:
    for value in (*stage.capability_intents, stage.objective):
        if _words(value) & _MUTATION_WORDS:
            return True
    return False


def _stage_produces_artifact(stage: StageSpec) -> bool:
    tokens: set[str] = set()
    for value in (*stage.capability_intents, stage.objective):
        tokens.update(_words(value))
    return bool(tokens & _ARTIFACT_WORDS)


def _conditional_side_effect(objective: str) -> bool:
    padded = f" {str(objective or '').lower()} "
    return any(marker in padded for marker in _CONDITIONAL_SIDE_EFFECT_MARKERS)


def _successful_worker_validator(spec: ValidatorSpec) -> ValidatorSpec:
    return replace(
        spec,
        kind=ValidatorKind.DETERMINISTIC,
        validator="worker_status",
        expected={
            "not_in": [
                "failed",
                "blocked",
                "denied",
                "rejected",
                "cancelled",
                "expired",
                "verification_failed",
                "unverified",
            ]
        },
        parameters={},
    )


def _provider_verified_validator(spec: ValidatorSpec) -> ValidatorSpec:
    return replace(
        spec,
        kind=ValidatorKind.PROVIDER,
        validator="provider_verified",
        expected={"verified": True},
        parameters={},
    )


class GroundedFactoryBlueprintCompiler(FactoryBlueprintCompiler):
    """Repair unsafe planner contracts without changing the planner's stage identity."""

    def _normalize(self, objective: str, payload: dict[str, Any]) -> FactoryBlueprint:
        blueprint = super()._normalize(objective, payload)
        stages = list(blueprint.graph.stages)
        by_id = {stage.id: stage for stage in stages}

        def ancestors(stage_id: str, seen: set[str] | None = None) -> set[str]:
            visited = set(seen or ())
            if stage_id in visited:
                return set()
            visited.add(stage_id)
            output: set[str] = set()
            stage = by_id[stage_id]
            for dependency in stage.dependencies:
                if dependency not in by_id:
                    continue
                output.add(dependency)
                output.update(ancestors(dependency, visited))
            return output

        # Mutating stages need the actual validated provenance that justifies the side
        # effect. Stage input refs are not transitive, so explicitly carry ancestors.
        graph_order = [stage.id for stage in stages]
        rewritten_stages: list[StageSpec] = []
        for stage in stages:
            if not _stage_is_mutating(stage):
                rewritten_stages.append(stage)
                continue
            inherited = ancestors(stage.id)
            ordered_ancestors = [item for item in graph_order if item in inherited]
            rewritten_stages.append(
                replace(
                    stage,
                    input_refs=tuple(
                        dict.fromkeys((*stage.input_refs, *ordered_ancestors))
                    )[:20],
                )
            )
        stages = rewritten_stages
        by_id = {stage.id: stage for stage in stages}

        attached: dict[str, list[StageSpec]] = {}
        for stage in stages:
            for validator_id in stage.validation_ids:
                attached.setdefault(validator_id, []).append(stage)

        # Artifact existence cannot prove task/email/calendar mutations. Conditional
        # side effects may truthfully produce zero actions, so they validate worker
        # completion rather than forcing a fabricated artifact/action.
        conditional = _conditional_side_effect(objective)
        validators: list[ValidatorSpec] = []
        for spec in blueprint.acceptance.validators:
            target_stages = attached.get(spec.id, [])
            invalid_artifact_validator = (
                spec.validator in {"artifact_exists", "artifact_count"}
                and target_stages
                and not any(_stage_produces_artifact(stage) for stage in target_stages)
            )
            if not invalid_artifact_validator:
                validators.append(spec)
                continue
            mutating = any(_stage_is_mutating(stage) for stage in target_stages)
            if mutating and not conditional:
                validators.append(_provider_verified_validator(spec))
            else:
                validators.append(_successful_worker_validator(spec))

        return FactoryBlueprint(
            objective=blueprint.objective,
            acceptance=AcceptanceContract(tuple(validators)),
            graph=StageGraph(tuple(stages)),
        )


class GroundedStageContextInjector(SafeStageContextInjector):
    """Never reinterpret provider operations as ambient workspace-history retrieval."""

    async def build(self, stage: StageSpec, **kwargs):
        return await super().build(
            replace(stage, context_intents=_explicit_history_intents(stage)),
            **kwargs,
        )


class GroundedAgentRuntimeWorker(SafeAgentRuntimeWorker):
    """Treat bounded-stage exhaustion as incomplete even when the last tool verified."""

    async def __call__(self, stage, capsule, attempt, defect):
        result = await super().__call__(stage, capsule, attempt, defect)
        summary = str(result.summary or "").strip()
        exhausted = summary.startswith(
            "Stopped after exhausting the bounded Factory stage budget."
        )
        if not exhausted:
            return result
        evidence = dict(result.evidence or {})
        evidence.update(
            {
                "terminal": False,
                "failure_class": "stage_execution_budget_exhausted",
                "incomplete_stage": True,
            }
        )
        return replace(
            result,
            status="failed",
            evidence=evidence,
        )


class GroundedControlPlaneValidator(ControlPlaneValidator):
    """Recognize provider verification preserved inside Factory execution truth."""

    @staticmethod
    def _provider_verified(spec: ValidatorSpec, result: StageWorkerResult) -> dict[str, Any]:
        direct = result.evidence.get("verification")
        if isinstance(direct, dict):
            observed = bool(direct.get("success") or direct.get("verified"))
        else:
            observed = bool(result.evidence.get("verified"))
        if not observed:
            truth = result.evidence.get("execution_truth")
            if isinstance(truth, dict):
                observed = bool(
                    truth.get("verified")
                    or (
                        truth.get("completed")
                        and str(truth.get("status") or "").upper() == "VERIFIED"
                    )
                )
        return {
            "passed": observed,
            "expected": True,
            "observed": observed,
            "evidence_refs": list(result.evidence_refs),
            "retryable": True,
        }


class GroundedDefectRepairPlanner(DefectRepairPlanner):
    """Never let a repair model manufacture business facts for a side effect."""

    async def __call__(
        self,
        stage: StageSpec,
        defect: Defect,
        repair_depth: int,
    ) -> StageSpec | None:
        if not defect.retryable:
            return None
        if _stage_is_mutating(stage):
            return replace(
                stage,
                objective=(
                    f"{stage.objective} Retry using only concrete facts present in the "
                    "supplied dependency_results and evidence. Do not invent people, "
                    "projects, action items, recipients, deadlines, amounts, or placeholder "
                    "content. If the evidence does not justify a real side effect, finish "
                    "without creating one. Follow the capability JSON schema literally; "
                    "enum fields must use only their documented values."
                )[:3000],
            )
        return await super().__call__(stage, defect, repair_depth)


class GroundedRuntimeAwareAgentFactoryControlPlane(RuntimeAwareAgentFactoryControlPlane):
    """Production Factory composition with grounded context, recovery and validation."""

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("compiler", GroundedFactoryBlueprintCompiler())
        kwargs.setdefault("repair_planner", GroundedDefectRepairPlanner())
        super().__init__(*args, **kwargs)

    def _runner(
        self,
        *,
        run_metadata: dict[str, Any],
        ledger,
        root_inference_budget,
    ) -> SafeFactoryStageRunner:
        validated_results: dict[str, StageWorkerResult] = {}
        injector = GroundedStageContextInjector(
            search=self.context_search,
            materialize=self.context_materialize,
            resolve_capabilities=self.capability_resolver,
            validated_results=validated_results,
        )
        worker = GroundedAgentRuntimeWorker(
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
        validator = GroundedControlPlaneValidator(
            python_test=self.python_validator,
            semantic=semantic_validator,
        )
        return SafeFactoryStageRunner(
            context_injector=injector,
            worker=worker,
            validator=validator,
            repair=repair_planner,
            event_sink=ledger.append,
            repair_budget=self.repair_budget,
            max_parallelism=self.max_parallelism,
            validated_results=validated_results,
        )
