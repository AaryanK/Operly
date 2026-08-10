"""Bounded build-test-diagnose-repair loop for harness-authored source.

Generated code never executes in the OPERLY control plane.  Each attempt is sent
to the isolated runner; only structured runner failure evidence is returned to the
coding model for a source-only repair.
"""
from __future__ import annotations

import json
import os
from typing import Any

from packages.coding_harness.build_service import submit_source_build
from packages.coding_harness.source_service import generate_source_for_plan, latest_source, repair_source_for_plan


REPAIRABLE_FAILURES = {
    "build_failure",
    "test_failure",
    "runtime_crash",
    "health_check_failure",
    "acceptance_test_failure",
}


def _failure_evidence(build) -> dict[str, Any]:
    try:
        result = json.loads(build.result_json or "{}")
    except Exception:
        result = {}
    evidence = result.get("failureEvidence") if isinstance(result, dict) else {}
    if not isinstance(evidence, dict):
        evidence = {"message": str(evidence)}
    return {
        **evidence,
        "classification": build.failure_classification or evidence.get("classification") or "unknown_failure",
        "buildState": build.state,
        "buildId": build.id,
        "attempt": build.attempt,
    }


def _repair_budget(value: int | None = None) -> int:
    configured = int(os.getenv("OPERLY_CODING_REPAIR_ATTEMPTS", "2")) if value is None else int(value)
    return max(0, min(configured, 4))


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
    """Return the final build, final source, and immutable repair-attempt metadata."""
    source = await latest_source(db, tenant_id, plan_row.id, plan_row.approved_version)
    if source is None:
        source, _ = await generate_source_for_plan(db, tenant_id, user_id, plan_row, plan, client=client)
        await db.commit()
        await db.refresh(source)

    repairs: list[dict[str, Any]] = []
    budget = _repair_budget(max_repairs)
    build = None

    for repair_number in range(0, budget + 1):
        attempt_key = idempotency_key if repair_number == 0 else f"{idempotency_key}-repair-{repair_number}"
        build = await submit_source_build(
            db,
            tenant_id,
            user_id,
            plan_row,
            plan,
            source,
            attempt_key,
            adapter=adapter,
        )
        if build.state == "preview_ready":
            return build, source, repairs

        evidence = _failure_evidence(build)
        classification = str(evidence.get("classification") or "unknown_failure")
        if repair_number >= budget or classification not in REPAIRABLE_FAILURES:
            return build, source, repairs

        previous_source = source
        source, result = await repair_source_for_plan(
            db,
            tenant_id,
            user_id,
            plan_row,
            plan,
            previous_source,
            evidence,
            client=client,
        )
        await db.commit()
        await db.refresh(source)
        repairs.append(
            {
                "repairNumber": repair_number + 1,
                "classification": classification,
                "failedBuildId": build.id,
                "fromSourceVersion": previous_source.source_version,
                "toSourceVersion": source.source_version,
                "changedPaths": result.changed_paths,
                "summary": result.summary,
            }
        )

    return build, source, repairs
