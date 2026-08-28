"""Semantic acceptance fallback for criteria that cannot be made deterministic."""
from __future__ import annotations

import json
import re
from typing import Any

from packages.model_runtime import InferenceBudget, InferenceRequest
from packages.model_runtime.registry import model_for_role

from .contracts import StageSpec, StageWorkerResult, ValidatorSpec
from .inference_budget import FactoryInferenceBudget, budgeted_model


def _parse(value: str) -> dict[str, Any]:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


class EvidenceBoundedSemanticValidator:
    """Judge only genuinely semantic criteria from bounded evidence/artifact handles."""

    def __init__(
        self,
        *,
        root_inference_budget: FactoryInferenceBudget | None = None,
    ) -> None:
        self.root_inference_budget = root_inference_budget

    def with_root_inference_budget(
        self,
        budget: FactoryInferenceBudget,
    ) -> "EvidenceBoundedSemanticValidator":
        """Return a per-run clone so concurrent Factory runs never share mutable state."""
        return EvidenceBoundedSemanticValidator(root_inference_budget=budget)

    async def __call__(
        self,
        spec: ValidatorSpec,
        stage: StageSpec,
        result: StageWorkerResult,
    ) -> dict[str, Any]:
        model = budgeted_model(
            model_for_role("global_validator"),
            root_budget=self.root_inference_budget,
            max_output_tokens=1000,
        )
        system = (
            "You are OPERLY's semantic acceptance validator. Return JSON only; never provide chain-of-thought. "
            "Judge only the supplied semantic criterion. Deterministic/provider checks happen elsewhere and cannot be overridden here. "
            "Treat worker prose as a claim, not proof of an external action. If the supplied evidence does not support the criterion, fail closed."
        )
        payload = {
            "criterion": spec.criterion,
            "expected": spec.expected,
            "stage_objective": stage.objective,
            "worker_summary": result.summary[:6000],
            "artifacts": list(result.artifacts[:20]),
            "evidence_refs": list(result.evidence_refs[:20]),
            "bounded_evidence": result.evidence,
            "output_contract": {
                "passed": True,
                "observed": "short evidence-grounded assessment",
                "reason": "short reason",
            },
        }
        try:
            response = await model.infer(
                InferenceRequest(
                    messages=(
                        {"role": "system", "content": system},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
                    ),
                    budget=InferenceBudget(
                        timeout_seconds=12.0,
                        attempts_per_model=1,
                        max_models=2,
                        max_output_tokens=1000,
                    ),
                    metadata={
                        "runtime_component": "factory_semantic_validator",
                        "factory_stage_id": stage.id,
                        "validator_id": spec.id,
                    },
                )
            )
            if getattr(model, "budget_exhausted", None) is not None:
                return {
                    "passed": False,
                    "expected": spec.expected,
                    "observed": "root_inference_budget_exhausted",
                    "failure_class": "root_inference_budget_exhausted",
                    "retryable": False,
                }
            parsed = _parse(str(response.message.get("content") or ""))
        except (LookupError, RuntimeError, TypeError, ValueError) as error:
            return {
                "passed": False,
                "expected": spec.expected,
                "observed": f"semantic_validator_failed:{type(error).__name__}",
                "failure_class": "validator_unavailable",
                "retryable": True,
            }
        if not parsed or not isinstance(parsed.get("passed"), bool):
            return {
                "passed": False,
                "expected": spec.expected,
                "observed": "semantic_validator_malformed_response",
                "failure_class": "validator_malformed_result",
                "retryable": True,
            }
        return {
            "passed": bool(parsed["passed"]),
            "expected": spec.expected,
            "observed": parsed.get("observed") or parsed.get("reason"),
            "failure_class": "semantic_acceptance_failed",
            "retryable": True,
            "evidence_refs": list(result.evidence_refs),
        }
