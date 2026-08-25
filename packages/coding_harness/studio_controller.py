"""Durable objective-owning controller for Studio software generation.

The coding agent remains the specialist that authors source.  AgentRunController is
the outer owner of the original product objective: it checkpoints each observation,
re-enters safely after worker restarts, and refuses to equate runner-green with
product-complete until deterministic objective/capability evidence also passes.
"""
from __future__ import annotations

import inspect
import json
from typing import Any, Awaitable, Callable

from sqlalchemy import select

from packages.agents.controller import AgentRunController
from packages.agents.run_state import RunPlan, RunTask
from packages.coding_harness.build_service import RunnerProfileUnsupported, submit_source_build
from packages.coding_harness.objective_audit import audit_generated_source
from packages.coding_harness.source_service import (
    generate_source_for_plan,
    latest_source,
    repair_source_for_plan,
)
from packages.database.custom_software_models import GeneratedSourceBundle
from packages.model_runtime import InferenceResult
from packages.runtime_plugins import FULLSTACK_RUNTIME_ID


REPAIRABLE_FAILURES = {
    "build_failure",
    "test_failure",
    "runtime_crash",
    "health_check_failure",
    "acceptance_test_failure",
}
ProgressCallback = Callable[[str, str, dict[str, Any]], Awaitable[None] | None]


def _plan_data(plan: Any) -> dict[str, Any]:
    if hasattr(plan, "model_dump"):
        value = plan.model_dump(mode="json")
        return value if isinstance(value, dict) else {}
    return dict(plan) if isinstance(plan, dict) else {}


def _objective(plan: Any) -> str:
    data = _plan_data(plan)
    provenance = data.get("provenance") if isinstance(data.get("provenance"), dict) else {}
    return str(
        provenance.get("originalPrompt")
        or data.get("summary")
        or data.get("primaryGoal")
        or data.get("projectName")
        or "Build and verify the approved Studio Solution."
    ).strip()


def _success_criteria(plan: Any) -> tuple[str, ...]:
    data = _plan_data(plan)
    criteria: list[str] = []
    for item in data.get("requirementLedger") or []:
        if not isinstance(item, dict) or not bool(item.get("mandatory", True)):
            continue
        requirement = str(item.get("normalizedMeaning") or item.get("exactText") or "").strip()
        if requirement:
            criteria.append(requirement[:800])
        for acceptance in item.get("acceptanceCriteria") or []:
            text = str(acceptance).strip()
            if text:
                criteria.append(text[:800])
    return tuple(dict.fromkeys(criteria))[:30] or (
        "Generated source materially implements the approved objective and passes isolated runner verification.",
    )


class _StudioPlanner:
    """Deterministic adapter: the approved software plan is already the plan."""

    def __init__(self, plan: Any) -> None:
        self.plan_record = plan

    async def plan(self, objective: str, trace_metadata=None) -> RunPlan:
        criteria = _success_criteria(self.plan_record)
        return RunPlan(
            objective=objective,
            success_criteria=criteria,
            planning_required=True,
            tasks=[
                RunTask(
                    id="studio-build-verify",
                    objective="Author, repair, run, and verify the approved software without losing the original requirements.",
                    success_criteria=criteria,
                    capability_intents=("studio.advance",),
                    assigned_role="coding_agent",
                )
            ],
        )

    async def replan(self, state, *, reason: str, trace_metadata=None) -> RunPlan:
        value = await self.plan(state.objective, trace_metadata=trace_metadata)
        value.revision = (state.plan.revision + 1) if state.plan else 1
        return value


class _StudioControlModel:
    """Deterministic AgentRuntime model; product reasoning stays in the coding agent."""

    id = "operly-studio-objective-controller"

    @staticmethod
    def _last_status(messages) -> str:
        for message in reversed(messages):
            if str(message.get("role") or "") != "tool":
                continue
            try:
                value = json.loads(str(message.get("content") or "{}"))
            except json.JSONDecodeError:
                return ""
            if isinstance(value, dict):
                return str(value.get("status") or "").upper()
        return ""

    async def infer(self, request) -> InferenceResult:
        status = self._last_status(request.messages)
        if status in {"VERIFIED", "FAILED"}:
            message = {
                "role": "assistant",
                "content": (
                    "Studio objective verification completed."
                    if status == "VERIFIED"
                    else "Studio reached a truthful terminal failure with evidence."
                ),
            }
        else:
            message = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "studio-advance",
                        "type": "function",
                        "function": {"name": "studio.advance", "arguments": "{}"},
                    }
                ],
            }
        return InferenceResult(
            message=message,
            model_resource_id=self.id,
            provider="deterministic",
            provider_model_id=self.id,
            latency_ms=0,
        )


def source_scoped_idempotency_key(base: str, source: Any) -> str:
    """Bind one runner idempotency key to exactly one immutable source bundle."""
    version = int(getattr(source, "source_version", 0) or 0)
    digest = str(getattr(source, "bundle_digest", "") or "").replace("sha256:", "")
    identity = digest[:20] or str(getattr(source, "id", "unknown"))[:20]
    return f"{base}:source:{version}:{identity}"


async def _notify(callback: ProgressCallback | None, stage: str, status: str, payload=None) -> None:
    if callback is None:
        return
    value = callback(stage, status, dict(payload or {}))
    if inspect.isawaitable(value):
        await value


def _provenance(row: Any) -> dict[str, Any]:
    try:
        value = json.loads(getattr(row, "provenance_json", "") or "{}")
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _structural_gap_count(audit: dict[str, Any]) -> int:
    """Count gaps that usually require architectural rewrites, not tiny repairs."""
    total = 0
    for key in ("behaviorGaps", "capabilityUsageGaps", "authorityGaps"):
        value = audit.get(key)
        if isinstance(value, (list, tuple)):
            total += len(value)
    return total


def _should_regenerate_stale_source(
    audit: dict[str, Any],
    *,
    generation_attempt: int,
    repairs: list[dict[str, Any]],
    already_regenerated: bool,
) -> bool:
    """Prefer a clean build when a retry inherits a broadly invalid old bundle.

    Incremental repair remains the default for first attempts and localized regressions.
    A later Solution retry gets one clean regeneration only when the deterministic audit
    reports multiple architectural product/capability gaps. This avoids spending the
    coding agent's bounded turn budget trying to transform a mock-heavy legacy tree one
    tiny edit at a time.
    """
    if audit.get("verified") or generation_attempt <= 1 or repairs or already_regenerated:
        return False
    behavior = audit.get("behaviorGaps") if isinstance(audit.get("behaviorGaps"), list) else []
    capabilities = audit.get("capabilityUsageGaps") if isinstance(audit.get("capabilityUsageGaps"), list) else []
    authority = audit.get("authorityGaps") if isinstance(audit.get("authorityGaps"), list) else []
    return _structural_gap_count(audit) >= 3 or (bool(behavior) and bool(capabilities or authority))


async def _repair_history(db, tenant_id: str, plan_row: Any, runtime_run_id: str) -> list[dict[str, Any]]:
    """Recover repair budget/history from immutable source provenance after restart."""
    try:
        query = (
            select(GeneratedSourceBundle)
            .where(
                GeneratedSourceBundle.tenant_id == tenant_id,
                GeneratedSourceBundle.plan_id == str(getattr(plan_row, "id", "")),
                GeneratedSourceBundle.plan_version == int(getattr(plan_row, "approved_version", 0) or 0),
            )
            .order_by(GeneratedSourceBundle.source_version.asc())
        )
        rows = list((await db.scalars(query)).all())
    except Exception:
        return []
    history = []
    for row in rows:
        provenance = _provenance(row)
        evidence = provenance.get("failureEvidence") if isinstance(provenance.get("failureEvidence"), dict) else {}
        if provenance.get("sourceOperation") != "runner_repair" or str(evidence.get("runtimeRunId") or "") != runtime_run_id:
            continue
        history.append(
            {
                "repairNumber": len(history) + 1,
                "classification": str(evidence.get("classification") or "unknown_failure"),
                "toSourceVersion": getattr(row, "source_version", None),
                "changedPaths": provenance.get("changedPaths") or [],
                "summary": provenance.get("summary"),
            }
        )
    return history


async def run_studio_generation(
    db,
    tenant_id: str,
    user_id: str,
    plan_row,
    plan,
    idempotency_key: str,
    *,
    adapter=None,
    client=None,
    max_repairs: int = 2,
    progress_callback: ProgressCallback | None = None,
    metadata: dict[str, Any],
    await_runner_build,
    failure_evidence,
):
    """Run one Solution attempt under the shared durable AgentRunController."""
    objective = _objective(plan)
    runtime_run_id = str(metadata.get("runtime_run_id") or idempotency_key)[:120]
    try:
        generation_attempt = max(1, int(metadata.get("generation_attempt") or 1))
    except (TypeError, ValueError):
        generation_attempt = 1
    repair_budget = max(0, min(int(max_repairs), 6))
    controller = AgentRunController(planner=_StudioPlanner(plan), max_replans=0)
    control_model = _StudioControlModel()
    final_result: dict[str, Any] = {}
    transient_history: list[dict[str, Any]] = []
    terminal_error: Exception | None = None
    regenerated_stale_source = False

    async def history() -> list[dict[str, Any]]:
        durable = await _repair_history(db, tenant_id, plan_row, runtime_run_id)
        return durable or list(transient_history)

    async def repair(source, evidence: dict[str, Any], failed_build_id=None):
        tagged = {**evidence, "runtimeRunId": runtime_run_id}
        current_history = await history()
        repair_number = len(current_history) + 1
        await _notify(progress_callback, "source_repair", "running", {"repairNumber": repair_number, "failureEvidence": tagged})
        updated, result = await repair_source_for_plan(
            db, tenant_id, user_id, plan_row, plan, source, tagged, client=client
        )
        await db.commit()
        await db.refresh(updated)
        row = {
            "repairNumber": repair_number,
            "classification": tagged.get("classification", "unknown_failure"),
            "failedBuildId": failed_build_id,
            "fromSourceVersion": getattr(source, "source_version", None),
            "toSourceVersion": getattr(updated, "source_version", None),
            "changedPaths": getattr(result, "changed_paths", []) or [],
            "summary": getattr(result, "summary", None),
        }
        transient_history.append(row)
        await _notify(progress_callback, "source_repair", "succeeded", row)
        return updated

    async def advance(_name: str, _arguments: dict[str, Any], _call_id: str | None):
        nonlocal terminal_error, regenerated_stale_source
        source = await latest_source(
            db,
            tenant_id,
            getattr(plan_row, "id", None),
            getattr(plan_row, "approved_version", None),
        )
        if source is None:
            await _notify(progress_callback, "source_generation", "running", {"planId": getattr(plan_row, "id", None)})
            source, result = await generate_source_for_plan(
                db, tenant_id, user_id, plan_row, plan, client=client
            )
            await db.commit()
            await db.refresh(source)
            await _notify(
                progress_callback,
                "source_generation",
                "succeeded",
                {"sourceBundleId": getattr(source, "id", None), "sourceVersion": getattr(source, "source_version", None), "changedPaths": getattr(result, "changed_paths", []) or []},
            )
            return {
                "ok": True,
                "status": "RUNNING",
                "artifact_refs": [str(getattr(source, "id", ""))],
                "message": "Initial source persisted; objective audit is next.",
            }

        repairs = await history()
        audit = audit_generated_source(plan, source)
        if not audit["verified"]:
            if _should_regenerate_stale_source(
                audit,
                generation_attempt=generation_attempt,
                repairs=repairs,
                already_regenerated=regenerated_stale_source,
            ):
                previous_version = getattr(source, "source_version", None)
                regenerated_stale_source = True
                await _notify(
                    progress_callback,
                    "source_generation",
                    "running",
                    {
                        "planId": getattr(plan_row, "id", None),
                        "fromSourceVersion": previous_version,
                        "reason": "structural_objective_reset",
                        "objectiveAudit": audit,
                    },
                )
                source, result = await generate_source_for_plan(
                    db, tenant_id, user_id, plan_row, plan, client=client
                )
                await db.commit()
                await db.refresh(source)
                await _notify(
                    progress_callback,
                    "source_generation",
                    "succeeded",
                    {
                        "sourceBundleId": getattr(source, "id", None),
                        "sourceVersion": getattr(source, "source_version", None),
                        "fromSourceVersion": previous_version,
                        "reason": "structural_objective_reset",
                        "changedPaths": getattr(result, "changed_paths", []) or [],
                    },
                )
                return {
                    "ok": True,
                    "status": "RUNNING",
                    "artifact_refs": [str(getattr(source, "id", ""))],
                    "objectiveAudit": audit,
                    "message": "Structurally stale source replaced with a clean generation; objective audit is next.",
                }
            if len(repairs) >= repair_budget:
                terminal_error = RuntimeError(f"Generated source does not satisfy approved objective: {audit['message']}")
                await _notify(progress_callback, "source_repair", "failed", audit)
                return {"ok": False, "status": "FAILED", "error": str(terminal_error), "objectiveAudit": audit}
            source = await repair(
                source,
                {
                    "classification": "objective_incomplete",
                    "message": audit["message"],
                    "objectiveAudit": audit,
                    "instruction": "Restore every missing approved requirement and consume declared Operly capabilities before optimizing runner mechanics.",
                },
            )
            return {
                "ok": True,
                "status": "RUNNING",
                "artifact_refs": [str(getattr(source, "id", ""))],
                "objectiveAudit": audit,
                "message": "Objective gaps repaired; re-auditing immutable source.",
            }

        attempt_key = source_scoped_idempotency_key(idempotency_key, source)
        await _notify(
            progress_callback,
            "runner_build",
            "running",
            {"idempotencyKey": attempt_key, "sourceBundleId": getattr(source, "id", None), "sourceVersion": getattr(source, "source_version", None)},
        )
        try:
            build = await submit_source_build(
                db,
                tenant_id,
                user_id,
                plan_row,
                plan,
                source,
                attempt_key,
                adapter=adapter,
                attempt=len(repairs) + 1,
            )
            build = await await_runner_build(db, build, adapter, progress_callback)
        except RunnerProfileUnsupported as error:
            if error.profile_id == FULLSTACK_RUNTIME_ID or len(repairs) >= repair_budget:
                terminal_error = error
                return {"ok": False, "status": "FAILED", "error": str(error), "objectiveAudit": audit}
            updated = await repair(
                source,
                {
                    "classification": "runner_profile_unsupported",
                    "message": str(error),
                    "generatedRuntime": error.profile_id,
                    "supportedProfiles": error.supported,
                    "instruction": "Preserve product behavior while adapting only runtime shape to a supported isolated profile.",
                },
            )
            return {"ok": True, "status": "RUNNING", "artifact_refs": [str(getattr(updated, "id", ""))]}

        repairs = await history()
        if build.state == "preview_ready":
            final_audit = audit_generated_source(plan, source)
            if not final_audit["verified"]:
                terminal_error = RuntimeError("Runner passed but the approved objective audit regressed")
                return {"ok": False, "status": "FAILED", "error": str(terminal_error), "objectiveAudit": final_audit}
            final_result.update({"build": build, "source": source, "repairs": repairs})
            await _notify(
                progress_callback,
                "preview_readiness",
                "succeeded",
                {"buildId": getattr(build, "id", None), "sourceBundleId": getattr(source, "id", None), "objectiveAudit": final_audit},
            )
            return {
                "ok": True,
                "status": "VERIFIED",
                "completed": True,
                "verified": True,
                "artifact_refs": [str(getattr(source, "id", "")), str(getattr(build, "id", ""))],
                "objectiveAudit": final_audit,
            }

        evidence = failure_evidence(build)
        classification = str(evidence.get("classification") or "unknown_failure")
        if classification in REPAIRABLE_FAILURES and len(repairs) < repair_budget:
            updated = await repair(source, evidence, failed_build_id=getattr(build, "id", None))
            return {
                "ok": True,
                "status": "RUNNING",
                "artifact_refs": [str(getattr(updated, "id", "")), str(getattr(build, "id", ""))],
                "failure": evidence,
            }

        final_result.update({"build": build, "source": source, "repairs": repairs})
        return {
            "ok": False,
            "status": "FAILED",
            "error": str(evidence.get("message") or classification),
            "artifact_refs": [str(getattr(source, "id", "")), str(getattr(build, "id", ""))],
            "failure": evidence,
            "objectiveAudit": audit,
        }

    schemas = lambda: [
        {
            "type": "function",
            "function": {
                "name": "studio.advance",
                "description": "Advance the durable Studio build/audit/runner lifecycle by one evidence-backed checkpoint.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }
    ]
    messages = [
        {
            "role": "user",
            "content": objective,
        }
    ]
    result = await controller.run(
        objective=objective,
        model=control_model,
        messages=messages,
        schemas=schemas,
        invoke=advance,
        max_steps=max(8, repair_budget * 3 + 6),
        inference_metadata=metadata,
    )
    if final_result:
        return final_result["build"], final_result["source"], final_result["repairs"]
    if terminal_error is not None:
        raise terminal_error
    raise RuntimeError(str(result.get("message") or "Studio objective controller ended without a build result"))


__all__ = ["run_studio_generation", "source_scoped_idempotency_key"]