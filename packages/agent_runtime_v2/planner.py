"""One-pass exact-capability planner for Agent Runtime v2."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from packages.model_runtime import InferenceBudget, InferenceRequest
from packages.model_runtime.registry import model_for_role

from .contracts import Plan, Step


@dataclass(frozen=True, slots=True)
class PlannedRun:
    plan: Plan
    input_tokens: int = 0
    output_tokens: int = 0


def _json_object(value: str) -> dict[str, Any]:
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


def _strings(value: Any, *, limit: int, chars: int) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    output: list[str] = []
    seen: set[str] = set()
    for raw in value[:limit]:
        clean = " ".join(str(raw or "").split()).strip()[:chars]
        if clean and clean not in seen:
            seen.add(clean)
            output.append(clean)
    return tuple(output)


class RuntimeV2Planner:
    """Translate the request directly into a tiny DAG over exact capability IDs.

    The planner never emits pseudo-intents. It sees a compact application-generated
    capability index and either selects exact IDs from that index or explicitly marks
    a requirement blocked. Authorization and execution still belong to the harness.
    """

    def __init__(self, *, max_steps: int = 8) -> None:
        self.max_steps = max(1, min(int(max_steps), 10))

    async def plan(
        self,
        *,
        objective: str,
        capability_catalog: list[dict[str, Any]],
        runtime_context: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> PlannedRun:
        catalog = [dict(row) for row in capability_catalog[:32] if isinstance(row, dict)]
        by_id = {
            str(row.get("id") or "").strip(): row
            for row in catalog
            if str(row.get("id") or "").strip()
        }
        system = (
            "You are OPERLY Runtime v2's execution planner. Return JSON only; never provide chain-of-thought. "
            "Create the smallest useful execution DAG for the literal request. Choose capability IDs ONLY from capability_catalog; never invent or paraphrase a capability ID. "
            "A step is a disposable worker station. Give it only the exact capabilities it needs. Split provider reads, cross-source reasoning, mutations, and final synthesis when later work depends on earlier observations. "
            "Preserve every negative constraint and conditional action rule from the request literally enough to enforce it. Do not create a duplicate representation of side effects: mutations are represented by mutating steps. "
            "If a required operation is unavailable in the catalog, put an object with requirement and reason in blocked instead of substituting an unrelated capability. "
            "Each step object must contain id, objective, capabilities, depends_on, and mutating. capabilities must be an array of exact IDs copied from capability_catalog; use an empty array for reasoning-only steps. "
            "The final step must be reasoning/synthesis only unless the user explicitly asks the final response itself to perform an action. "
            "Prefer 2-5 steps for multi-source workflows and one step for genuinely simple requests."
        )
        payload = {
            "request": objective[:16_000],
            "runtime_context": {
                key: value
                for key, value in runtime_context.items()
                if key in {"now", "timezone", "surface", "channel", "workspace_mode"}
            },
            "capability_catalog": catalog,
            # Empty structural shape only. Do not seed model-authored content with
            # literal examples that can be copied into the plan.
            "output_shape": {
                "goal": "",
                "constraints": [],
                "blocked": [],
                "steps": [],
                "final_step_id": "",
            },
            "limits": {"max_steps": self.max_steps},
        }
        model = model_for_role("requirements_analyst")
        result = await model.infer(
            InferenceRequest(
                messages=(
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False, default=str),
                    },
                ),
                budget=InferenceBudget(
                    timeout_seconds=20.0,
                    attempts_per_model=1,
                    max_models=2,
                    max_output_tokens=2200,
                ),
                metadata={
                    **dict(metadata or {}),
                    "runtime_component": "agent_runtime_v2_planner",
                },
            )
        )
        parsed = _json_object(str(result.message.get("content") or ""))
        if not parsed:
            raise ValueError("Runtime v2 planner returned invalid JSON")

        constraints = _strings(parsed.get("constraints"), limit=16, chars=700)
        blocked: list[dict[str, Any]] = []
        for raw in list(parsed.get("blocked") or [])[:8]:
            if not isinstance(raw, dict):
                continue
            requirement = " ".join(str(raw.get("requirement") or "").split()).strip()[:700]
            reason = " ".join(str(raw.get("reason") or "").split()).strip()[:700]
            if requirement:
                blocked.append({"requirement": requirement, "reason": reason})

        steps: list[Step] = []
        seen: set[str] = set()
        for index, raw in enumerate(list(parsed.get("steps") or [])[: self.max_steps], start=1):
            if not isinstance(raw, dict):
                continue
            step_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(raw.get("id") or f"step-{index}"))[:80].strip("-")
            if not step_id or step_id in seen:
                step_id = f"step-{index}"
            seen.add(step_id)
            step_objective = " ".join(str(raw.get("objective") or "").split()).strip()[:3000]
            if not step_objective:
                continue
            requested_caps = _strings(raw.get("capabilities"), limit=6, chars=160)
            exact_caps: list[str] = []
            for capability_id in requested_caps:
                row = by_id.get(capability_id)
                if row is None:
                    blocked.append(
                        {
                            "requirement": f"Step {step_id} requested {capability_id}",
                            "reason": "planner_selected_capability_outside_catalog",
                        }
                    )
                    continue
                if row.get("available") is False:
                    blocked.append(
                        {
                            "requirement": f"Step {step_id} requires {capability_id}",
                            "reason": str(row.get("unavailable_reason") or "capability_unavailable")[:700],
                        }
                    )
                    continue
                exact_caps.append(capability_id)
            steps.append(
                Step(
                    id=step_id,
                    objective=step_objective,
                    capabilities=tuple(exact_caps),
                    depends_on=_strings(raw.get("depends_on"), limit=8, chars=80),
                    mutating=bool(raw.get("mutating")),
                )
            )

        if not steps:
            raise ValueError("Runtime v2 planner returned no executable steps")
        step_ids = {step.id for step in steps}
        normalized: list[Step] = []
        for step in steps:
            deps = tuple(item for item in step.depends_on if item in step_ids and item != step.id)
            normalized.append(
                Step(
                    id=step.id,
                    objective=step.objective,
                    capabilities=step.capabilities,
                    depends_on=deps,
                    mutating=step.mutating,
                )
            )
        final_step_id = str(parsed.get("final_step_id") or normalized[-1].id).strip()
        if final_step_id not in {step.id for step in normalized}:
            final_step_id = normalized[-1].id

        usage = result.usage
        return PlannedRun(
            plan=Plan(
                goal=" ".join(str(parsed.get("goal") or objective).split()).strip()[:3000],
                constraints=constraints,
                steps=tuple(normalized),
                final_step_id=final_step_id,
                blocked=tuple(blocked),
            ),
            input_tokens=max(0, int(getattr(usage, "input_tokens", 0) or 0)),
            output_tokens=max(0, int(getattr(usage, "output_tokens", 0) or 0)),
        )
