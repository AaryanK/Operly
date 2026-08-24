"""Bounded build-test-diagnose-repair loop for harness-authored source.

Generated code never executes in the OPERLY control plane. Each attempt is sent
to the isolated runner; structured capability or failure evidence is returned to
the same coding model for the smallest source-only repair.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
from typing import Any, Awaitable, Callable

from packages.coding_harness.build_service import RunnerProfileUnsupported, submit_source_build
from packages.coding_harness.source_service import generate_source_for_plan, latest_source, repair_source_for_plan
from packages.custom_software.runner_service import refresh_build
from packages.database.model_trace import ensure_model_trace_sink
from packages.model_runtime.trace_context import RuntimeTraceEvent, emit_runtime_trace_event, runtime_trace_scope
from packages.runtime_plugins import FULLSTACK_RUNTIME_ID


REPAIRABLE_FAILURES = {"build_failure", "test_failure", "runtime_crash", "health_check_failure", "acceptance_test_failure"}
SETTLED_BUILD_STATES = {
    "preview_ready", "failed", "build_failed", "tests_failed", "start_failed",
    "health_check_failed", "acceptance_failed", "provision_failed", "dependency_failed",
    "static_analysis_failed", "repair_failed", "cancelled", "timed_out",
    "security_blocked", "resource_exceeded", "cleaned", "completed",
}
ProgressCallback = Callable[[str, str, dict[str, Any]], Awaitable[None] | None]


def _failure_evidence(build) -> dict[str, Any]:
    try:
        result = json.loads(getattr(build, "result_json", "") or "{}")
    except Exception:
        result = {}
    evidence = result.get("failureEvidence") if isinstance(result, dict) else {}
    if not isinstance(evidence, dict):
        evidence = {"message": str(evidence)}
    return {
        **evidence,
        "classification": getattr(build, "failure_classification", None) or evidence.get("classification") or "unknown_failure",
        "buildState": getattr(build, "state", None),
        "buildId": getattr(build, "id", None),
        "attempt": getattr(build, "attempt", None),
    }


def _stage_for_build(build) -> str:
    evidence = _failure_evidence(build)
    classification = str(evidence.get("classification") or "").lower()
    state = str(getattr(build, "state", "") or "").lower()
    if classification == "test_failure" or state == "tests_failed":
        return "runner_test"
    if classification in {"health_check_failure", "acceptance_test_failure"} or state in {
        "health_check_failed", "acceptance_failed", "preview_ready", "completed"
    }:
        return "acceptance_test"
    return "runner_build"


async def _progress(callback: ProgressCallback | None, stage: str, status: str, payload: dict[str, Any] | None = None) -> None:
    if callback is None:
        return
    result = callback(stage, status, dict(payload or {}))
    if inspect.isawaitable(result):
        await result


def _repair_budget(value: int | None = None) -> int:
    configured = int(os.getenv("OPERLY_CODING_REPAIR_ATTEMPTS", "2")) if value is None else int(value)
    return max(0, min(configured, 4))


def _runner_poll_interval() -> float:
    try:
        return max(0.25, min(float(os.getenv("OPERLY_RUNNER_POLL_INTERVAL_SECONDS", "1")), 10.0))
    except ValueError:
        return 1.0


def _runner_poll_timeout() -> float:
    try:
        return max(30.0, min(float(os.getenv("OPERLY_RUNNER_POLL_TIMEOUT_SECONDS", "900")), 3600.0))
    except ValueError:
        return 900.0


def _trace_metadata(tenant_id: str, user_id: str, plan_row, idempotency_key: str) -> dict[str, Any]:
    solution_id = ""
    attempt = 1
    prefix = "solution:"
    marker = ":generated-build:"
    if idempotency_key.startswith(prefix) and marker in idempotency_key:
        solution_id = idempotency_key[len(prefix):].split(marker, 1)[0]
        raw_attempt = idempotency_key.split(marker, 1)[1].split("-", 1)[0]
        try:
            attempt = max(1, int(raw_attempt))
        except ValueError:
            attempt = 1
    plan_id = str(getattr(plan_row, "id", "unknown"))
    conversation_id = f"solution:{solution_id}" if solution_id else f"software-plan:{plan_id}"
    run_id = f"solution:{solution_id}:attempt:{attempt}" if solution_id else f"software-plan:{plan_id}"
    return {
        "conversation_id": conversation_id,
        "runtime_run_id": run_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "principal_id": f"user:{user_id}",
        "channel": "solution",
        "surface": "solution_generation",
        "runtime_component": "coding_harness",
        "solution_id": solution_id or None,
        "software_plan_id": plan_id,
        "generation_attempt": attempt,
    }


async def _trace(event_type: str, payload: Any = None, *, phase: str = "event", classification: str | None = None, retryable: bool | None = None, resource_id: str = "coding_harness") -> None:
    await emit_runtime_trace_event(RuntimeTraceEvent(event_type=event_type, payload=payload, phase=phase, classification=classification, retryable=retryable, resource_id=resource_id))


def _trace_items(value: Any) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        return []
    rows = []
    for item in value[-400:]:
        if isinstance(item, dict):
            rows.append(item)
        elif hasattr(item, "__dict__"):
            rows.append(dict(item.__dict__))
        else:
            rows.append(str(item))
    return rows


def _source_trace_payload(source, result=None) -> dict[str, Any]:
    try:
        manifest = json.loads(getattr(source, "manifest_json", "") or "{}")
    except Exception:
        manifest = {}
    try:
        provenance = json.loads(getattr(source, "provenance_json", "") or "{}")
    except Exception:
        provenance = {}
    payload = {
        "sourceBundleId": getattr(source, "id", None),
        "sourceVersion": getattr(source, "source_version", None),
        "bundleDigest": getattr(source, "bundle_digest", None),
        "planId": getattr(source, "plan_id", None),
        "planVersion": getattr(source, "plan_version", None),
        "files": manifest.get("files", []) if isinstance(manifest, dict) else [],
        "totalBytes": manifest.get("totalBytes", 0) if isinstance(manifest, dict) else 0,
    }
    if isinstance(provenance, dict) and provenance:
        payload.update({
            "modelProvider": provenance.get("modelProvider"), "modelId": provenance.get("modelId"),
            "summary": provenance.get("summary"), "changedPaths": provenance.get("changedPaths") or [],
            "verification": provenance.get("verificationIntent") or [], "toolTrace": _trace_items(provenance.get("toolTrace") or []),
        })
    if result is not None:
        payload.update({
            "modelProvider": getattr(result, "model_provider", None), "modelId": getattr(result, "model_id", None),
            "summary": getattr(result, "summary", None), "changedPaths": getattr(result, "changed_paths", []) or [],
            "verification": getattr(result, "verification", []) or [], "toolTrace": _trace_items(getattr(result, "trace", []) or []),
        })
    return payload


async def _await_runner_build(db, build, adapter, progress_callback: ProgressCallback | None = None):
    if build.state in SETTLED_BUILD_STATES:
        evidence = _failure_evidence(build)
        await _progress(progress_callback, _stage_for_build(build), "settled", evidence)
        await _trace("runner.build.settled", evidence, resource_id="sandbox_runner")
        return build
    deadline = time.monotonic() + _runner_poll_timeout()
    previous_state = build.state
    await _progress(progress_callback, "runner_build", "running", {"buildId": getattr(build, "id", None), "state": build.state})
    await _trace("runner.build.polling", {"buildId": getattr(build, "id", None), "state": build.state}, resource_id="sandbox_runner")
    while build.state not in SETTLED_BUILD_STATES:
        if time.monotonic() >= deadline:
            payload = {"buildId": getattr(build, "id", None), "state": build.state}
            await _progress(progress_callback, "runner_build", "failed", payload)
            await _trace("runner.build.timeout", payload, phase="error", classification="runner_poll_timeout", retryable=True, resource_id="sandbox_runner")
            raise TimeoutError(f"Isolated runner build {getattr(build, 'id', 'unknown')} did not settle before the orchestration deadline")
        await asyncio.sleep(_runner_poll_interval())
        build = await refresh_build(db, build, adapter=adapter)
        if build.state != previous_state:
            payload = {"buildId": getattr(build, "id", None), "from": previous_state, "to": build.state, "runnerJobId": getattr(build, "runner_job_id", None)}
            await _progress(progress_callback, _stage_for_build(build), "running", payload)
            await _trace("runner.build.state_changed", payload, resource_id="sandbox_runner")
            previous_state = build.state
    evidence = _failure_evidence(build)
    await _progress(progress_callback, _stage_for_build(build), "succeeded" if build.state == "preview_ready" else "failed", evidence)
    await _trace("runner.build.settled", evidence, resource_id="sandbox_runner")
    return build


async def _repair(db, tenant_id, user_id, plan_row, plan, source, evidence, client, repairs, repair_number, failed_build_id=None, progress_callback: ProgressCallback | None = None):
    previous_source = source
    payload = {"repairNumber": repair_number, "failedBuildId": failed_build_id, "failureEvidence": evidence}
    await _progress(progress_callback, "source_repair", "running", payload)
    await _trace("coding_agent.repair.started", payload, resource_id="coding_agent")
    source, result = await repair_source_for_plan(db, tenant_id, user_id, plan_row, plan, previous_source, evidence, client=client)
    await db.commit()
    await db.refresh(source)
    repairs.append({
        "repairNumber": repair_number,
        "classification": evidence.get("classification", "unknown_failure"),
        "failedBuildId": failed_build_id,
        "fromSourceVersion": getattr(previous_source, "source_version", None),
        "toSourceVersion": getattr(source, "source_version", None),
        "changedPaths": getattr(result, "changed_paths", []) or [],
        "summary": getattr(result, "summary", None),
    })
    completed_payload = _source_trace_payload(source, result)
    completed_payload["repairNumber"] = repair_number
    await _progress(progress_callback, "source_repair", "succeeded", completed_payload)
    await _trace("coding_agent.repair.completed", completed_payload, resource_id="coding_agent")
    return source


async def build_with_repair(
    db, tenant_id: str, user_id: str, plan_row, plan, idempotency_key: str, *,
    adapter=None, client=None, max_repairs: int | None = None,
    progress_callback: ProgressCallback | None = None,
):
    """Return final build, final source, and immutable repair-attempt metadata."""
    ensure_model_trace_sink()
    metadata = _trace_metadata(tenant_id, user_id, plan_row, idempotency_key)
    with runtime_trace_scope(metadata):
        await _trace("coding_harness.started", {"idempotencyKey": idempotency_key, "planId": getattr(plan_row, "id", None), "planVersion": getattr(plan_row, "approved_version", None)})
        try:
            source = await latest_source(db, tenant_id, getattr(plan_row, "id", None), getattr(plan_row, "approved_version", None))
            if source is None:
                await _progress(progress_callback, "source_generation", "running", {"planId": getattr(plan_row, "id", None)})
                await _trace("coding_agent.source_generation.started", {"planId": getattr(plan_row, "id", None)}, resource_id="coding_agent")
                source, result = await generate_source_for_plan(db, tenant_id, user_id, plan_row, plan, client=client)
                await db.commit()
                await db.refresh(source)
                payload = _source_trace_payload(source, result)
                await _progress(progress_callback, "source_generation", "succeeded", payload)
                await _trace("coding_agent.source_generation.completed", payload, resource_id="coding_agent")
            else:
                payload = _source_trace_payload(source)
                await _progress(progress_callback, "source_generation", "reused", payload)
                await _trace("coding_agent.source_reused", payload, resource_id="coding_agent")

            repairs: list[dict[str, Any]] = []
            budget = _repair_budget(max_repairs)
            used_repairs = 0
            build = None

            while True:
                attempt_key = idempotency_key if used_repairs == 0 else f"{idempotency_key}-repair-{used_repairs}"
                try:
                    submit_payload = {"idempotencyKey": attempt_key, "sourceBundleId": getattr(source, "id", None), "sourceVersion": getattr(source, "source_version", None), "bundleDigest": getattr(source, "bundle_digest", None), "repairNumber": used_repairs}
                    await _progress(progress_callback, "runner_build", "running", submit_payload)
                    await _trace("runner.submit.started", submit_payload, resource_id="sandbox_runner")
                    build = await submit_source_build(db, tenant_id, user_id, plan_row, plan, source, attempt_key, adapter=adapter, attempt=used_repairs + 1)
                    await _trace("runner.submit.persisted", {"buildId": getattr(build, "id", None), "state": getattr(build, "state", None), "runnerJobId": getattr(build, "runner_job_id", None), "classification": getattr(build, "failure_classification", None)}, resource_id="sandbox_runner")
                    build = await _await_runner_build(db, build, adapter, progress_callback)
                except RunnerProfileUnsupported as error:
                    await _progress(progress_callback, "runner_build", "failed", {"profile": error.profile_id, "supported": error.supported, "message": str(error)})
                    await _trace("runner.profile.unsupported", {"profile": error.profile_id, "supported": error.supported, "message": str(error)}, phase="error", classification="runner_profile_unsupported", retryable=False, resource_id="sandbox_runner")
                    if error.profile_id == FULLSTACK_RUNTIME_ID:
                        raise
                    if used_repairs >= budget:
                        raise
                    used_repairs += 1
                    evidence = {"classification": "runner_profile_unsupported", "message": str(error), "generatedRuntime": error.profile_id, "supportedProfiles": error.supported, "instruction": "Preserve product behavior but adapt the source tree to one supported isolated runtime profile using only its declared standard-library/dependency-free contract."}
                    source = await _repair(db, tenant_id, user_id, plan_row, plan, source, evidence, client, repairs, used_repairs, progress_callback=progress_callback)
                    continue

                if build.state == "preview_ready":
                    await _progress(progress_callback, "preview_readiness", "succeeded", {"buildId": getattr(build, "id", None), "sourceBundleId": getattr(source, "id", None), "repairs": repairs})
                    await _trace("coding_harness.completed", {"buildId": getattr(build, "id", None), "state": build.state, "sourceBundleId": getattr(source, "id", None), "repairs": repairs}, phase="success")
                    return build, source, repairs

                evidence = _failure_evidence(build)
                classification = str(evidence.get("classification") or "unknown_failure")
                if used_repairs >= budget or classification not in REPAIRABLE_FAILURES:
                    await _trace("coding_harness.failed", evidence, phase="error", classification=classification, retryable=True)
                    return build, source, repairs

                used_repairs += 1
                source = await _repair(db, tenant_id, user_id, plan_row, plan, source, evidence, client, repairs, used_repairs, failed_build_id=getattr(build, "id", None), progress_callback=progress_callback)
        except Exception as error:
            await _trace("coding_harness.exception", {"type": type(error).__name__, "message": str(error)}, phase="error", classification=type(error).__name__, retryable=True)
            raise
