"""Fresh defect-driven repair planning for the factory control plane."""
from __future__ import annotations

import json
import re
from typing import Any

from packages.model_runtime import InferenceBudget, InferenceRequest
from packages.model_runtime.registry import model_for_role

from .contracts import Defect, StageSpec, bounded_strings
from .inference_budget import FactoryInferenceBudget, budgeted_model


def _parse(value: str) -> dict[str, Any]:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                pass
        return {}


class DefectRepairPlanner:
    """Ask a fresh reasoning worker for a different bounded method after a defect."""

    def __init__(
        self,
        *,
        root_inference_budget: FactoryInferenceBudget | None = None,
    ) -> None:
        self.root_inference_budget = root_inference_budget

    def with_root_inference_budget(
        self,
        budget: FactoryInferenceBudget,
    ) -> "DefectRepairPlanner":
        """Return a per-run clone so shared control-plane instances stay concurrency-safe."""
        return DefectRepairPlanner(root_inference_budget=budget)

    async def __call__(
        self,
        stage: StageSpec,
        defect: Defect,
        repair_depth: int,
    ) -> StageSpec | None:
        model = budgeted_model(
            model_for_role("requirements_analyst"),
            root_budget=self.root_inference_budget,
            max_output_tokens=1400,
        )
        system = (
            "You are OPERLY's defect repair planner. Return JSON only; never provide chain-of-thought. "
            "You receive one bounded stage and deterministic failure evidence. Propose a materially different repair strategy only if the defect is retryable. "
            "Do not change the stage identity, dependencies, validation IDs, authorization, or root objective. "
            "Do not invent capability IDs or resource IDs; use context/capability intents in plain language. "
            "A worker-exit failure does not mean its earlier capability calls failed: verified observations are retained in stage working state and will be supplied to the next worker. "
            "Do not replace a relevant read operation with a semantically unrelated operation merely to be different (for example, do not replace recent-email search/read with draft listing). "
            "For worker-exit failures, revise the objective toward the remaining work and preserve the provider family/operation intents that are still required; let the next worker reuse completed observations instead of restarting them. "
            "Do not ask to repeat the same method that produced the same failure. Return {\"abort\":true,...} if evidence shows the objective cannot currently be satisfied."
        )
        payload = {
            "stage": stage.as_dict(),
            "defect": defect.as_dict(),
            "repair_depth": repair_depth,
            "output_contract": {
                "abort": False,
                "reason": "short reason",
                "objective": "revised bounded stage objective describing a different method",
                "context_intents": [],
                "capability_intents": [],
                "assigned_role": stage.assigned_role,
            },
        }
        try:
            result = await model.infer(
                InferenceRequest(
                    messages=(
                        {"role": "system", "content": system},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
                    ),
                    budget=InferenceBudget(
                        timeout_seconds=12.0,
                        attempts_per_model=1,
                        max_models=2,
                        max_output_tokens=1400,
                    ),
                    metadata={
                        "runtime_component": "factory_defect_repair",
                        "factory_stage_id": stage.id,
                        "repair_depth": repair_depth,
                    },
                )
            )
            if getattr(model, "budget_exhausted", None) is not None:
                return None
            parsed = _parse(str(result.message.get("content") or ""))
        except (LookupError, RuntimeError, TypeError, ValueError):
            return None
        if not parsed or bool(parsed.get("abort")):
            return None
        objective = " ".join(str(parsed.get("objective") or "").split()).strip()[:3000]
        if not objective:
            return None
        return StageSpec(
            id=stage.id,
            objective=objective,
            dependencies=stage.dependencies,
            context_intents=bounded_strings(parsed.get("context_intents"), limit=8, item_chars=500),
            capability_intents=bounded_strings(parsed.get("capability_intents"), limit=8, item_chars=500),
            input_refs=stage.input_refs,
            validation_ids=stage.validation_ids,
            assigned_role=str(parsed.get("assigned_role") or stage.assigned_role)[:80],
            can_parallelize=stage.can_parallelize,
            max_output_chars=stage.max_output_chars,
        )
