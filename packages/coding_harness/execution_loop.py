"""Bounded build-test-diagnose-repair loop for harness-authored source.

Generated code never executes in the OPERLY control plane. Each attempt is sent
to the isolated runner; structured capability or failure evidence is returned to
the same coding model for the smallest source-only repair.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from packages.coding_harness.build_service import RunnerProfileUnsupported, submit_source_build
from packages.coding_harness.source_service import generate_source_for_plan, latest_source, repair_source_for_plan
from packages.custom_software.runner_service import refresh_build
from packages.database.model_trace import ensure_model_trace_sink
from packages.model_runtime.trace_context import RuntimeTraceEvent, emit_runtime_trace_event, runtime_trace_scope
from packages.runtime_plugins import FULLSTACK_RUNTIME_ID


REPAIRABLE_FAILURES = {"build_failure", "test_failure", "runtime_crash", "health_check_failure", "acceptance_test_failure"}
SETTLED_BUILD_STATES = {
    "preview_ready",
    "failed",
    "build_failed",
    "tests_failed",
    "start_failed",
    "health_check_failed",
    "acceptance_failed",
    "provision_failed",
    "dependency_failed",
    "static_analysis_failed",
    "repair_failed",
    "cancelled",
    "timed_out",
    "security_blocked",
    "resource_exceeded",
    "cleaned",
    "completed",
}


def _failure_evidence(build) -> dict[str, Any]:
    try:
        result = json.loads(build.result_json or "{}")
    except Exception:
        result = {}
    evidence = result.get("failureEvidence") if isinstance(result, dict) else {}
    if not isinstance(evidence, dict):
        evidence = {"message": str(evidence)}
    return {**evidence, "classification": build.failure_classification or evidence.get("classification") or "unknown_failure", "buildState": build.state, "buildId": build.id, "attempt": build.attempt}


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
    conversation_id = f"solution:{solution_id}" if solution_id else f"software-plan:{plan_row.id}"
    run_id = f"solution:{solution_id}:attempt:{attempt}" if solution_id else f"software-plan:{plan_row.id}"
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
        "software_plan_id": str(plan_row.id),
        "generation_attempt": attempt,
    }


async def _trace(event_type: str, payload: Any = None, *, phase: str = "event", classification: str | None = None, retryable: bool | None = None, resource_id: str = "coding_harness") -> None:
    await emit_runtime_trace_event(
        RuntimeTraceEvent(
            event_type=event_type,
            payload=payload,
            phase=phase,
            classification=classification,
            retryable=retryable,
            resource_id=resource_id,
        )
    )


def _source_trace_payload(source, result=None) -> dict[str, Any]:
    try:
        manifest = json.loads(source.manifest_json or "{}")
    except Exception:
        manifest = {}
    payload = {
        "sourceBundleId": source.id,
        "sourceVersion": source.source_version,
        "bundleDigest": source.bundle_digest,
        "planId": source.plan_id,
        "planVersion": source.plan_version,
        "files": manifest.get("files", []),
        "totalBytes": manifest.get("totalBytes", 0),
    }
    if result is not None:
        payload.update(
            {
                "modelProvider": result.model_provider,
                "modelId": result.model_id,
                "summary": result.summary,
                "changedPaths": result.changed_paths,
                "verification": result.verification,
                "toolTrace": [item.__dict__ for item in result.trace[-400:]],
            }
        )
    return payload


async def _await_runner_build(db, build, adapter):
    """Poll an asynchronous runner until it reaches an evidence-bearing state."""
    if build.state in SETTLED_BUILD_STATES:
        await _trace("runner.build.settled", _failure_evidence(build), resource_id="sandbox_runner")
        return build
    deadline = time.monotonic() + _runner_poll_timeout()
    previous_state = build.state
    await _trace("runner.build.polling", {"buildId": build.id, "state": build.state}, resource_id="sandbox_runner")
    while build.state not in SETTLED_BUILD_STATES:
        if time.monotonic() >= deadline:
            await _trace(
                "runner.build.timeout",
                {"buildId": build.id, "state": build.state},
                phase="error",
                classification="runner_poll_timeout",
                retryable=True,
                resource_id="sandbox_runner",
            )
            raise TimeoutError(
                f"Isolated runner build {build.id} did not settle before the orchestration deadline"
            )
        await asyncio.sleep(_runner_poll_interval())
        build = await refresh_build(db, build, adapter=adapter)
        if build.state != previous_state:
            await _trace(
                "runner.build.state_changed",
                {"buildId": build.id, "from": previous_state, "to": build.state, "runnerJobId": build.runner_job_id},
                resource_id="sandbox_runner",
            )
            previous_state = build.state
    await _trace("runner.build.settled", _failure_evidence(build), resource_id="sandbox_runner")
    return build


async def _repair(db, tenant_id, user_id, plan_row, plan, source, evidence, client, repairs, repair_number, failed_build_id=None):
    previous_source = source
    await _trace(
        "coding_agent.repair.started",
        {"repairNumber": repair_number, "failedBuildId": failed_build_id, "failureEvidence": evidence},
        resource_id="coding_agent",
    )
    source, result = await repair_source_for_plan(db, tenant_id, user_id, plan_row, plan, previous_source, evidence, client=client)
    await db.commit()
    await db.refresh(source)
    repairs.append({
        "repairNumber": repair_number,
        "classification": evidence.get("classification", "unknown_failure"),
        "failedBuildId": failed_build_id,
        "fromSourceVersion": previous_source.source_version,
        "toSourceVersion": source.source_version,
        "changedPaths": result.changed_paths,
        "summary": result.summary,
    })
    await _trace("coding_agent.repair.completed", _source_trace_payload(source, result), resource_id="coding_agent")
    return source


async def build_with_repair(
    db,
    tenant_id: str,
    user_id: str,
    plan_row,
    plan,
    idempotency_key: str,
    *,
    adapter=None,
    client=None,
    max_repairs: int | None = None,
):
    """Return final build, final source, and immutable repair-attempt metadata."""
    ensure_model_trace_sink()
    metadata = _trace_metadata(tenant_id, user_id, plan_row, idempotency_key)
    with runtime_trace_scope(metadata):
        await _trace(
            "coding_harness.started",
            {"idempotencyKey": idempotency_key, "planId": plan_row.id, "planVersion": plan_row.approved_version},
        )
        try:
            source = await latest_source(db, tenant_id, plan_row.id, plan_row.approved_version)
            if source is None:
                await _trace("coding_agent.source_generation.started", {"planId": plan_row.id}, resource_id="coding_agent")
                source, result = await generate_source_for_plan(db, tenant_id, user_id, plan_row, plan, client=client)
                await db.commit()
                await db.refresh(source)
                await _trace("coding_agent.source_generation.completed", _source_trace_payload(source, result), resource_id="coding_agent")
            else:
                await _trace("coding_agent.source_reused", _source_trace_payload(source), resource_id="coding_agent")

            repairs: list[dict[str, Any]] = []
            budget = _repair_budget(max_repairs)
            used_repairs = 0
            build = None

            while True:
                attempt_key = idempotency_key if used_repairs == 0 else f"{idempotency_key}-repair-{used_repairs}"
                try:
                    await _trace(
                        "runner.submit.started",
                        {
                            "idempotencyKey": attempt_key,
                            "sourceBundleId": source.id,
                            "sourceVersion": source.source_version,
                            "bundleDigest": source.bundle_digest,
                            "repairNumber": used_repairs,
                        },
                        resource_id="sandbox_runner",
                    )
                    build = await submit_source_build(
                        db,
                        tenant_id,
                        user_id,
                        plan_row,
                        plan,
                        source,
                        attempt_key,
                        adapter=adapter,
                        attempt=used_repairs + 1,
                    )
                    await _trace(
                        "runner.submit.persisted",
                        {"buildId": build.id, "state": build.state, "runnerJobId": build.runner_job_id, "classification": build.failure_classification},
                        resource_id="sandbox_runner",
                    )
                    build = await _await_runner_build(db, build, adapter)
                except RunnerProfileUnsupported as error:
                    await _trace(
                        "runner.profile.unsupported",
                        {"profile": error.profile_id, "supported": error.supported, "message": str(error)},
                        phase="error",
                        classification="runner_profile_unsupported",
                        retryable=False,
                        resource_id="sandbox_runner",
                    )
                    if error.profile_id == FULLSTACK_RUNTIME_ID:
                        raise
                    if used_repairs >= budget:
                        raise
                    used_repairs += 1
                    evidence = {
                        "classification": "runner_profile_unsupported",
                        "message": str(error),
                        "generatedRuntime": error.profile_id,
                        "supportedProfiles": error.supported,
                        "instruction": "Preserve product behavior but adapt the source tree to one supported isolated runtime profile using only its declared standard-library/dependency-free contract.",
                    }
                    source = await _repair(db, tenant_id, user_id, plan_row, plan, source, evidence, client, repairs, used_repairs)
                    continue

                if build.state == "preview_ready":
                    await _trace(
                        "coding_harness.completed",
                        {"buildId": build.id, "state": build.state, "sourceBundleId": source.id, "repairs": repairs},
                        phase="success",
                    )
                    return build, source, repairs

                evidence = _failure_evidence(build)
                classification = str(evidence.get("classification") or "unknown_failure")
                if used_repairs >= budget or classification not in REPAIRABLE_FAILURES:
                    await _trace(
                        "coding_harness.failed",
                        evidence,
                        phase="error",
                        classification=classification,
                        retryable=True,
                    )
                    return build, source, repairs

                used_repairs += 1
                source = await _repair(db, tenant_id, user_id, plan_row, plan, source, evidence, client, repairs, used_repairs, failed_build_id=build.id)
        except Exception as error:
            await _trace(
                "coding_harness.exception",
                {"type": type(error).__name__, "message": str(error)},
                phase="error",
                classification=type(error).__name__,
                retryable=True,
            )
            raise
