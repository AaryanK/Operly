"""Compile a user request into a small immutable factory blueprint before execution.

This compiler receives the literal request and small ingress metadata only. It is not a
workspace-context loader and cannot grant capabilities. It emits context/capability
*intents* that the application-controlled injector/harness resolves later under the
trusted execution context.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from packages.model_runtime import InferenceBudget, InferenceRequest
from packages.model_runtime.conversation_policy import is_trivial_conversation
from packages.model_runtime.registry import model_for_role

from .contracts import (
    AcceptanceContract,
    ObjectiveSpec,
    StageGraph,
    StageSpec,
    ValidatorKind,
    ValidatorSpec,
    bounded_strings,
)
from .inference_budget import FactoryInferenceBudget, budgeted_model


@dataclass(frozen=True, slots=True)
class FactoryBlueprint:
    objective: ObjectiveSpec
    acceptance: AcceptanceContract
    graph: StageGraph

    def as_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective.as_dict(),
            "acceptance": self.acceptance.as_dict(),
            "graph": self.graph.as_dict(),
        }


def _parse_json_object(value: str) -> dict[str, Any]:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


class FactoryBlueprintCompiler:
    """Use one bounded reasoning pass to define success before workers execute."""

    _KNOWN_VALIDATORS = {
        "worker_status",
        "evidence_present",
        "field_equals",
        "field_gte",
        "artifact_exists",
        "artifact_count",
        "provider_verified",
        "python_test",
        "semantic_evidence",
    }

    def __init__(self, *, max_stages: int = 10, max_validators: int = 16) -> None:
        self.max_stages = max(1, min(int(max_stages), 16))
        self.max_validators = max(1, min(int(max_validators), 24))

    @staticmethod
    def _fallback(
        objective: str,
        *,
        resolve_capabilities: bool = True,
    ) -> FactoryBlueprint:
        """Return one capable station when planning is unnecessary or unavailable.

        A planner failure must not silently turn an actionable request into a
        reasoning-only worker. For non-trivial objectives, use the literal objective as
        a plain-language capability intent; the application-side resolver still applies
        installation, authority and surface policy before any schema is exposed.
        """
        validator = ValidatorSpec(
            id="root-result",
            criterion="The bounded worker returns a non-failed result for the requested objective.",
            kind=ValidatorKind.DETERMINISTIC,
            validator="worker_status",
            expected={"not_in": ["failed", "blocked"]},
        )
        stage = StageSpec(
            id="stage-1",
            objective=objective,
            capability_intents=(objective[:500],) if resolve_capabilities else (),
            validation_ids=(validator.id,),
        )
        return FactoryBlueprint(
            ObjectiveSpec(objective=objective, deliverables=(objective[:700],)),
            AcceptanceContract((validator,)),
            StageGraph((stage,)),
        )

    async def _infer(
        self,
        objective: str,
        ingress: dict[str, Any],
        *,
        root_inference_budget: FactoryInferenceBudget | None = None,
    ) -> dict[str, Any]:
        model = budgeted_model(
            model_for_role("requirements_analyst"),
            root_budget=root_inference_budget,
            max_output_tokens=3200,
        )
        system = (
            "You are OPERLY's factory blueprint compiler. Return JSON only; never provide chain-of-thought. "
            "Before any execution, convert the literal user request into: (1) an immutable objective spec, "
            "(2) observable acceptance validators, and (3) the minimum stage DAG needed to produce the result. "
            "Prefer deterministic validators over semantic judgment. Do not invent capability IDs, workspace data, resource IDs, permissions, or connector state. "
            "Stages may declare context_intents and capability_intents in plain language; the application resolves them later under authorization. "
            "Do not put retrieved content into the plan. Do not make a stage depend on another unless its output is actually required. "
            "Use python_test only when a requirement is objectively testable but needs computation/file inspection; provide a concise test_intent, not executable code. "
            "For purely conversational/informational requests use one stage and a minimal validator."
        )
        payload = {
            "request": objective[:20_000],
            "ingress_metadata": {
                key: value
                for key, value in ingress.items()
                if key in {
                    "attachment_count",
                    "attachment_names",
                    "channel",
                    "surface",
                    "has_images",
                }
            },
            "output_contract": {
                "objective": {
                    "deliverables": ["literal deliverable"],
                    "constraints": ["literal constraint"],
                    "required_side_effects": ["send/publish/update/etc only when explicitly required"],
                },
                "validators": [
                    {
                        "id": "short-id",
                        "criterion": "observable success criterion",
                        "kind": "deterministic | provider | semantic",
                        "validator": "worker_status | evidence_present | field_equals | field_gte | artifact_exists | artifact_count | provider_verified | python_test | semantic_evidence",
                        "expected": {},
                        "parameters": {"test_intent": "only for python_test"},
                        "required": True,
                    }
                ],
                "stages": [
                    {
                        "id": "stage-1",
                        "objective": "bounded station objective",
                        "dependencies": [],
                        "context_intents": [],
                        "capability_intents": [],
                        "input_refs": [],
                        "validation_ids": ["validator-id"],
                        "assigned_role": "business_agent",
                        "can_parallelize": False,
                    }
                ],
            },
            "limits": {
                "max_stages": self.max_stages,
                "max_validators": self.max_validators,
            },
        }
        result = await model.infer(
            InferenceRequest(
                messages=(
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
                ),
                budget=InferenceBudget(
                    timeout_seconds=15.0,
                    attempts_per_model=1,
                    max_models=2,
                    max_output_tokens=3200,
                ),
                metadata={"runtime_component": "factory_blueprint_compiler"},
            )
        )
        return _parse_json_object(str(result.message.get("content") or ""))

    def _normalize(self, objective: str, payload: dict[str, Any]) -> FactoryBlueprint:
        objective_data = _dict(payload.get("objective"))
        objective_spec = ObjectiveSpec(
            objective=objective,
            deliverables=bounded_strings(objective_data.get("deliverables"), limit=12, item_chars=1000),
            constraints=bounded_strings(objective_data.get("constraints"), limit=12, item_chars=1000),
            required_side_effects=bounded_strings(
                objective_data.get("required_side_effects"), limit=12, item_chars=500
            ),
        )

        validators: list[ValidatorSpec] = []
        seen_validators: set[str] = set()
        for index, raw in enumerate(list(payload.get("validators") or [])[: self.max_validators], start=1):
            if not isinstance(raw, dict):
                continue
            validator_id = str(raw.get("id") or f"validator-{index}").strip()[:80]
            if not validator_id or validator_id in seen_validators:
                validator_id = f"validator-{index}"
            seen_validators.add(validator_id)
            criterion = " ".join(str(raw.get("criterion") or "").split()).strip()[:1200]
            if not criterion:
                continue
            try:
                kind = ValidatorKind(str(raw.get("kind") or "deterministic").lower())
            except ValueError:
                kind = ValidatorKind.SEMANTIC
            validator = str(raw.get("validator") or "evidence_present").strip().lower()
            if validator not in self._KNOWN_VALIDATORS:
                validator = "semantic_evidence"
                kind = ValidatorKind.SEMANTIC
            if validator == "semantic_evidence":
                kind = ValidatorKind.SEMANTIC
            validators.append(
                ValidatorSpec(
                    id=validator_id,
                    criterion=criterion,
                    kind=kind,
                    validator=validator,
                    expected=_dict(raw.get("expected")),
                    parameters=_dict(raw.get("parameters")),
                    required=bool(raw.get("required", True)),
                )
            )
        if not validators:
            return self._fallback(objective)

        validator_ids = {item.id for item in validators}
        stages: list[StageSpec] = []
        seen_stages: set[str] = set()
        for index, raw in enumerate(list(payload.get("stages") or [])[: self.max_stages], start=1):
            if not isinstance(raw, dict):
                continue
            stage_id = str(raw.get("id") or f"stage-{index}").strip()[:80]
            if not stage_id or stage_id in seen_stages:
                stage_id = f"stage-{index}"
            seen_stages.add(stage_id)
            stage_objective = " ".join(str(raw.get("objective") or "").split()).strip()[:3000]
            if not stage_objective:
                continue
            stage_validators = tuple(
                item
                for item in bounded_strings(raw.get("validation_ids"), limit=12, item_chars=80)
                if item in validator_ids
            )
            stages.append(
                StageSpec(
                    id=stage_id,
                    objective=stage_objective,
                    dependencies=bounded_strings(raw.get("dependencies"), limit=10, item_chars=80),
                    context_intents=bounded_strings(raw.get("context_intents"), limit=8, item_chars=500),
                    capability_intents=bounded_strings(raw.get("capability_intents"), limit=8, item_chars=500),
                    input_refs=bounded_strings(raw.get("input_refs"), limit=20, item_chars=160),
                    validation_ids=stage_validators,
                    assigned_role=str(raw.get("assigned_role") or "business_agent")[:80],
                    can_parallelize=bool(raw.get("can_parallelize")),
                )
            )
        if not stages:
            return self._fallback(objective)

        stage_ids = {stage.id for stage in stages}
        normalized_stages = []
        for stage in stages:
            dependencies = tuple(
                dependency
                for dependency in stage.dependencies
                if dependency in stage_ids and dependency != stage.id
            )
            normalized_stages.append(
                StageSpec(
                    id=stage.id,
                    objective=stage.objective,
                    dependencies=dependencies,
                    context_intents=stage.context_intents,
                    capability_intents=stage.capability_intents,
                    input_refs=stage.input_refs,
                    validation_ids=stage.validation_ids,
                    assigned_role=stage.assigned_role,
                    can_parallelize=stage.can_parallelize,
                    max_output_chars=stage.max_output_chars,
                )
            )

        attached = {item for stage in normalized_stages for item in stage.validation_ids}
        missing = tuple(item.id for item in validators if item.required and item.id not in attached)
        if missing:
            terminal = normalized_stages[-1]
            normalized_stages[-1] = StageSpec(
                id=terminal.id,
                objective=terminal.objective,
                dependencies=terminal.dependencies,
                context_intents=terminal.context_intents,
                capability_intents=terminal.capability_intents,
                input_refs=terminal.input_refs,
                validation_ids=tuple(dict.fromkeys((*terminal.validation_ids, *missing))),
                assigned_role=terminal.assigned_role,
                can_parallelize=terminal.can_parallelize,
                max_output_chars=terminal.max_output_chars,
            )

        graph = StageGraph(tuple(normalized_stages))
        return FactoryBlueprint(objective_spec, AcceptanceContract(tuple(validators)), graph)

    async def compile(
        self,
        objective: str,
        *,
        ingress_metadata: dict[str, Any] | None = None,
        root_inference_budget: FactoryInferenceBudget | None = None,
    ) -> FactoryBlueprint:
        clean = " ".join(str(objective or "").split()).strip()
        if not clean:
            raise ValueError("Factory objective is required")
        if is_trivial_conversation(clean):
            return self._fallback(clean, resolve_capabilities=False)
        try:
            payload = await self._infer(
                clean,
                dict(ingress_metadata or {}),
                root_inference_budget=root_inference_budget,
            )
            if not payload:
                return self._fallback(clean)
            return self._normalize(clean, payload)
        except (LookupError, RuntimeError, TypeError, ValueError):
            return self._fallback(clean)
