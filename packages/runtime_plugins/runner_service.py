"""Durable control-plane orchestration for external isolated runners.

Source authoring and repair deliberately live in ``packages.software_projects.coding``.
This module owns only runner lifecycle state, durable events, result ingestion,
preview access and cleanup. Keeping that boundary explicit prevents source-generation
logic from becoming a second runtime architecture.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from sqlalchemy import func, select

from packages.runtime_plugins.runner_adapters import ExternalRunnerAdapter
from packages.runtime_plugins.runner_contracts import BuildSubmission
from packages.database.custom_software_models import (
    RunnerArtifactRecord,
    RunnerBuildEvent,
    RunnerBuildRecord,
    RunnerPreviewRecord,
)

STATES = {
    "created",
    "queued",
    "provisioning",
    "provision_failed",
    "source_staging",
    "dependency_resolution",
    "dependency_failed",
    "static_analysis",
    "static_analysis_failed",
    "building",
    "build_failed",
    "testing",
    "tests_failed",
    "starting",
    "start_failed",
    "health_checking",
    "health_check_failed",
    "acceptance_testing",
    "acceptance_failed",
    "running",
    "preview_ready",
    "repair_requested",
    "repairing",
    "repair_failed",
    "cancel_requested",
    "cancelled",
    "timed_out",
    "security_blocked",
    "resource_exceeded",
    "cleaning",
    "cleaned",
    "completed",
    "failed",
}
TERMINAL = {
    "cleaned",
    "completed",
    "failed",
    "cancelled",
    "timed_out",
    "security_blocked",
    "resource_exceeded",
}
TRANSITIONS = {
    "created": {"queued", "cancel_requested"},
    "queued": {"provisioning", "failed", "cancel_requested"},
    "provisioning": {"source_staging", "provision_failed", "cancel_requested"},
    "source_staging": {"dependency_resolution", "static_analysis", "security_blocked", "failed"},
    "dependency_resolution": {"static_analysis", "dependency_failed", "security_blocked"},
    "static_analysis": {"building", "static_analysis_failed", "security_blocked"},
    "building": {"testing", "build_failed", "resource_exceeded", "timed_out"},
    "testing": {"starting", "tests_failed", "resource_exceeded", "timed_out"},
    "starting": {"health_checking", "start_failed", "resource_exceeded"},
    "health_checking": {"acceptance_testing", "health_check_failed", "timed_out"},
    "acceptance_testing": {"running", "acceptance_failed", "timed_out"},
    "running": {"preview_ready", "cancel_requested", "cleaning"},
    "preview_ready": {"repair_requested", "cancel_requested", "cleaning", "completed"},
    "repair_requested": {"repairing", "cancel_requested"},
    "repairing": {"queued", "repair_failed", "security_blocked"},
    "cancel_requested": {"cancelled"},
    "cancelled": {"cleaning"},
    "completed": {"cleaning"},
    "failed": {"repair_requested", "cleaning"},
    "build_failed": {"repair_requested", "cleaning", "failed"},
    "tests_failed": {"repair_requested", "cleaning", "failed"},
    "start_failed": {"repair_requested", "cleaning", "failed"},
    "health_check_failed": {"repair_requested", "cleaning", "failed"},
    "acceptance_failed": {"repair_requested", "cleaning", "failed"},
    "cleaning": {"cleaned"},
    "provision_failed": {"failed", "cleaning"},
    "dependency_failed": {"failed", "repair_requested", "cleaning"},
    "static_analysis_failed": {"failed", "repair_requested", "cleaning"},
    "repair_failed": {"failed", "cleaning"},
}
FAILURES = {
    "provision_failed": "provision_failure",
    "dependency_failed": "dependency_failure",
    "static_analysis_failed": "security_policy_violation",
    "build_failed": "build_failure",
    "tests_failed": "test_failure",
    "start_failed": "runtime_crash",
    "health_check_failed": "health_check_failure",
    "acceptance_failed": "acceptance_test_failure",
    "security_blocked": "security_policy_violation",
    "resource_exceeded": "resource_violation",
    "timed_out": "resource_violation",
    "failed": "unknown_failure",
}
RUNNER_PHASE_ORDER = (
    "provisioning",
    "source_staging",
    "dependency_resolution",
    "static_analysis",
    "building",
    "testing",
    "starting",
    "health_checking",
    "acceptance_testing",
    "running",
)
RUNNER_PHASE_RANK = {state: index for index, state in enumerate(RUNNER_PHASE_ORDER)}
PHASE_FAILURE_STATE = {
    "provisioning": "provision_failed",
    "dependency_resolution": "dependency_failed",
    "static_analysis": "static_analysis_failed",
    "building": "build_failed",
    "testing": "tests_failed",
    "starting": "start_failed",
    "health_checking": "health_check_failed",
    "acceptance_testing": "acceptance_failed",
}
CLASSIFICATION_FAILURE_STATE = {
    "test_failure": "tests_failed",
    "build_failure": "build_failed",
    "runtime_crash": "start_failed",
    "health_check_failure": "health_check_failed",
    "acceptance_test_failure": "acceptance_failed",
    "security_policy_violation": "security_blocked",
    "resource_violation": "resource_exceeded",
}
CLASSIFICATION_ALIASES = {
    "health_failure": "health_check_failure",
    "health_failed": "health_check_failure",
    "healthcheck_failure": "health_check_failure",
    "health_check_failed": "health_check_failure",
    "acceptance_failure": "acceptance_test_failure",
    "acceptance_check_failure": "acceptance_test_failure",
    "acceptance_failed": "acceptance_test_failure",
}
GENERIC_FAILURE_MESSAGES = {
    "test_failure": {"Python tests failed", "Node tests failed"},
    "build_failure": {"Python static analysis failed", "Frontend build failed"},
}
FAILURE_EVENT_STATES = {
    "test_failure": {"testing"},
    "build_failure": {"building", "static_analysis"},
}


class RunnerStateError(ValueError):
    pass


def _redact(value):
    return re.sub(
        r"(?i)(bearer\s+|token[=:]\s*|secret[=:]\s*)[^\s,]+",
        r"\1[REDACTED]",
        str(value),
    )[:4000]


def _failure_evidence(result):
    evidence = result.get("failureEvidence", {}) if isinstance(result, dict) else {}
    return evidence if isinstance(evidence, dict) else {}


def _normalize_failure_classification(value):
    classification = str(value or "").strip().lower()
    return CLASSIFICATION_ALIASES.get(classification, classification)


def _failure_classification(result):
    evidence = _failure_evidence(result)
    explicit = _normalize_failure_classification(evidence.get("classification"))
    if explicit:
        return explicit
    for key, classification in (
        ("buildSuccess", "build_failure"),
        ("testSuccess", "test_failure"),
        ("processStartSuccess", "runtime_crash"),
        ("healthCheckSuccess", "health_check_failure"),
        ("acceptanceCheckSuccess", "acceptance_test_failure"),
    ):
        if result.get(key) is False:
            return classification
    return "unknown_failure"


def _enrich_failure_evidence(result, response, classification):
    """Promote bounded runner gate output when the result only has a generic error.

    The isolated runner already returns bounded per-phase event messages. Older
    runner builds summarized test/build failures as only ``Python tests failed`` or
    similar, which left the coding repair loop without the assertion/trace it needs.
    Preserve the canonical result shape while copying the last relevant gate event
    into failureEvidence. Runner events remain the source of truth and are redacted
    before they become durable/model-visible evidence.
    """
    if not isinstance(result, dict) or not classification:
        return result
    evidence = result.get("failureEvidence")
    if not isinstance(evidence, dict):
        return result
    generic = GENERIC_FAILURE_MESSAGES.get(classification)
    states = FAILURE_EVENT_STATES.get(classification)
    if not generic or not states:
        return result
    error = str(evidence.get("error") or "").strip()
    if error and error not in generic:
        return result
    events = response.get("events") if isinstance(response, dict) else None
    if not isinstance(events, list):
        return result
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        if str(event.get("state") or "") not in states:
            continue
        message = _redact(event.get("message") or "")
        if not message or message in generic:
            continue
        evidence["error"] = message
        evidence["runnerEventState"] = str(event.get("state") or "")[:80]
        if event.get("exitCode") is not None:
            evidence["runnerExitCode"] = event.get("exitCode")
        break
    return result


def _failure_state_from_result(result):
    classification = _failure_classification(result)
    return CLASSIFICATION_FAILURE_STATE.get(classification, "failed")


def _event_rank(state):
    return RUNNER_PHASE_RANK.get(str(state or ""), -1)


def _runner_failure_state(result, response, current_state):
    classification = _failure_classification(result)
    classified_state = CLASSIFICATION_FAILURE_STATE.get(classification)
    events = response.get("events") if isinstance(response, dict) else None
    observed_states = [
        str(event.get("state") or "")
        for event in events or []
        if isinstance(event, dict) and event.get("state")
    ]
    if classified_state:
        expected_phase = next(
            (phase for phase, failure_state in PHASE_FAILURE_STATE.items() if failure_state == classified_state),
            None,
        )
        if expected_phase and expected_phase in observed_states:
            return classified_state
    for state in reversed(observed_states):
        if state in PHASE_FAILURE_STATE:
            return PHASE_FAILURE_STATE[state]
    if current_state in PHASE_FAILURE_STATE:
        return PHASE_FAILURE_STATE[current_state]
    return classified_state or "failed"


def _result_has_phase_failure(result, *, current_state):
    if not isinstance(result, dict):
        return True
    if bool(result.get("success")):
        return False
    return _failure_state_from_result(result) != "failed" or current_state not in {"running", "preview_ready"}


async def create_build(db, *, tenant_id: str, plan_id: str, source_version: int, idempotency_key: str, submission: BuildSubmission):
    existing = await db.scalar(
        select(RunnerBuildRecord).where(
            RunnerBuildRecord.tenant_id == tenant_id,
            RunnerBuildRecord.idempotency_key == idempotency_key,
        )
    )
    if existing:
        return existing
    row = RunnerBuildRecord(
        tenant_id=tenant_id,
        plan_id=plan_id,
        source_version=source_version,
        idempotency_key=idempotency_key,
        state="created",
        request_json=json.dumps(submission.model_dump(mode="json"), sort_keys=True),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    await db.flush()
    return row


async def transition(db, row: RunnerBuildRecord, to_state: str, *, detail: dict | None = None):
    current = row.state
    if to_state == current:
        return row
    allowed = TRANSITIONS.get(current, set())
    if to_state not in allowed:
        raise RunnerStateError(f"Invalid runner transition {current} -> {to_state}")
    row.state = to_state
    row.updated_at = datetime.utcnow()
    if to_state in FAILURE_EVENT_STATES.get(FAILURES.get(to_state, ""), set()):
        row.failure_classification = FAILURES.get(to_state)
    elif to_state in FAILURES:
        row.failure_classification = FAILURES[to_state]
    db.add(
        RunnerBuildEvent(
            tenant_id=row.tenant_id,
            build_id=row.id,
            state=to_state,
            detail_json=json.dumps(detail or {}, ensure_ascii=False, sort_keys=True, default=str)[:32000],
        )
    )
    await db.flush()
    return row


async def process_external_build(db, row: RunnerBuildRecord, *, adapter: ExternalRunnerAdapter | None = None):
    adapter = adapter or ExternalRunnerAdapter()
    submission = BuildSubmission.model_validate_json(row.request_json)
    if row.state == "created":
        await transition(db, row, "queued")
    if row.state == "queued":
        await transition(db, row, "provisioning")
    response = await adapter.submit(submission)
    result = response.get("result") if isinstance(response, dict) else None
    result = result if isinstance(result, dict) else {}
    classification = _failure_classification(result)
    _enrich_failure_evidence(result, response, classification)
    observed = [
        str(event.get("state") or "")
        for event in (response.get("events") or [])
        if isinstance(event, dict) and event.get("state")
    ]
    for state in observed:
        if state == row.state:
            continue
        if state in TRANSITIONS.get(row.state, set()):
            await transition(db, row, state)
    if bool(result.get("success")):
        if row.state == "running":
            await transition(db, row, "preview_ready")
    else:
        target = _runner_failure_state(result, response, row.state)
        if target != row.state and target in TRANSITIONS.get(row.state, set()):
            await transition(db, row, target, detail=_failure_evidence(result))
        row.failure_classification = classification
        row.result_json = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
        await db.flush()
        return row
    row.result_json = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
    await db.flush()
    return row


async def latest_preview(db, *, tenant_id: str, build_id: str):
    return await db.scalar(
        select(RunnerPreviewRecord)
        .where(
            RunnerPreviewRecord.tenant_id == tenant_id,
            RunnerPreviewRecord.build_id == build_id,
            RunnerPreviewRecord.expires_at > datetime.utcnow(),
        )
        .order_by(RunnerPreviewRecord.created_at.desc())
        .limit(1)
    )


async def append_artifact(db, *, tenant_id: str, build_id: str, kind: str, reference: str, digest: str | None = None):
    row = RunnerArtifactRecord(
        tenant_id=tenant_id,
        build_id=build_id,
        kind=kind,
        reference=reference,
        digest=digest,
    )
    db.add(row)
    await db.flush()
    return row
