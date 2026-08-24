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


async def _await_runner_build(db, build, adapter):
    """Poll an asynchronous runner until it reaches an evidence-bearing state.

    The production runner returns HTTP 202/queued immediately. Treating that
    transport acknowledgement as a failed build would make every real isolated
    build fail before it starts, so the coding loop owns bounded polling.
    """
    if build.state in SETTLED_BUILD_STATES:
        return build
    deadline = time.monotonic() + _runner_poll_timeout()
    while build.state not in SETTLED_BUILD_STATES:
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Isolated runner build {build.id} did not settle before the orchestration deadline"
            )
        await asyncio.sleep(_runner_poll_interval())
        build = await refresh_build(db, build, adapter=adapter)
    return build


async def _repair(db, tenant_id, user_id, plan_row, plan, source, evidence, client, repairs, repair_number, failed_build_id=None):
    previous_source = source
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
    source = await latest_source(db, tenant_id, plan_row.id, plan_row.approved_version)
    if source is None:
        source, _ = await generate_source_for_plan(db, tenant_id, user_id, plan_row, plan, client=client)
        await db.commit()
        await db.refresh(source)

    repairs: list[dict[str, Any]] = []
    budget = _repair_budget(max_repairs)
    used_repairs = 0
    build = None

    while True:
        attempt_key = idempotency_key if used_repairs == 0 else f"{idempotency_key}-repair-{used_repairs}"
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
                attempt=used_repairs + 1,
            )
            build = await _await_runner_build(db, build, adapter)
        except RunnerProfileUnsupported as error:
            # Missing full-stack executor support is infrastructure truth, not a
            # source defect. Never ask the coding model to silently collapse a
            # full-stack application into the old dependency-free profiles.
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
            return build, source, repairs

        evidence = _failure_evidence(build)
        classification = str(evidence.get("classification") or "unknown_failure")
        if used_repairs >= budget or classification not in REPAIRABLE_FAILURES:
            return build, source, repairs

        used_repairs += 1
        source = await _repair(db, tenant_id, user_id, plan_row, plan, source, evidence, client, repairs, used_repairs, failed_build_id=build.id)
