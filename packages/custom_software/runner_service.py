"""Durable control-plane orchestration for external isolated runners.

Source authoring and repair deliberately live in ``packages.coding_harness``.  This
module owns only runner lifecycle state, durable events, result ingestion, preview
access and cleanup.  Keeping that boundary explicit prevents demo/source-generation
logic from becoming a second coding architecture.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from sqlalchemy import func, select

from packages.custom_software.runner_adapters import ExternalRunnerAdapter
from packages.custom_software.runner_contracts import BuildSubmission
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


def _failure_classification(result):
    evidence = _failure_evidence(result)
    explicit = str(evidence.get("classification") or "").strip()
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


def _failure_state_for(current_state, classification):
    classified = CLASSIFICATION_FAILURE_STATE.get(classification)
    allowed = TRANSITIONS.get(current_state, set())
    if classified and (classified == current_state or classified in allowed):
        return classified
    phase_failure = PHASE_FAILURE_STATE.get(current_state)
    if phase_failure and phase_failure in allowed:
        return phase_failure
    if "failed" in allowed:
        return "failed"
    if classified:
        return classified
    return "failed"


async def _event(db, row, state, event_type="lifecycle", message="", details=None):
    if state not in STATES:
        raise RunnerStateError("Unknown runner state")
    if state != row.state and state not in TRANSITIONS.get(row.state, set()):
        raise RunnerStateError(f"Invalid runner transition {row.state} -> {state}")
    sequence = (
        await db.scalar(
            select(func.max(RunnerBuildEvent.sequence)).where(RunnerBuildEvent.build_id == row.id)
        )
        or 0
    ) + 1
    row.state = state
    if state in FAILURES:
        row.failure_classification = FAILURES[state]
    safe = {key: _redact(value) for key, value in (details or {}).items()}
    db.add(
        RunnerBuildEvent(
            tenant_id=row.tenant_id,
            build_id=row.id,
            sequence=sequence,
            state=state,
            event_type=event_type,
            message=_redact(message or state),
            details_json=json.dumps(safe),
        )
    )


async def _record_runner_failure_observation(db, row, state, result, classification):
    """Persist remote failure evidence before lifecycle normalization can fail."""
    evidence = _failure_evidence(result)
    flags = {
        key: result.get(key)
        for key in (
            "buildSuccess",
            "testSuccess",
            "processStartSuccess",
            "healthCheckSuccess",
            "acceptanceCheckSuccess",
            "previewAvailable",
        )
        if key in result
    }
    row.result_json = json.dumps(result)
    if classification:
        row.failure_classification = classification
    await _event(
        db,
        row,
        row.state,
        event_type="runner_failure_observed",
        message="Runner returned failure evidence",
        details={
            "remoteState": state,
            "localState": row.state,
            "classification": classification,
            "failureEvidence": json.dumps(evidence, ensure_ascii=False, sort_keys=True),
            "resultFlags": json.dumps(flags, ensure_ascii=False, sort_keys=True),
        },
    )
    # Commit this checkpoint separately. If a later state transition is invalid,
    # the real runner evidence remains durable instead of being masked by the
    # orchestration exception.
    await db.commit()
    await db.refresh(row)


async def _advance_runner_path(db, row, path):
    """Advance only forward from the durable local checkpoint."""
    current_rank = RUNNER_PHASE_RANK.get(row.state, -1)
    for phase in path:
        phase_rank = RUNNER_PHASE_RANK.get(phase, -1)
        if phase_rank <= current_rank:
            continue
        if phase not in TRANSITIONS.get(row.state, set()):
            # Optional phases (for example dependency resolution) may not be in
            # every path. Never replay backwards or manufacture an illegal hop.
            continue
        await _event(db, row, phase, message=f"Runner phase: {phase}")
        current_rank = phase_rank


async def apply_runner_response(db, row, response, submission):
    row.runner_job_id = response.get("jobId", row.runner_job_id)
    result = response.get("result", {})
    state = response.get("state", "failed")
    if state in {
        "created",
        "queued",
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
    } and not result:
        if state != row.state:
            await _event(db, row, state, event_type="runner_poll", message=f"Runner state: {state}")
        row.result_json = json.dumps({"remoteState": state})
        await db.commit()
        await db.refresh(row)
        return row

    preview_ready = state == "preview_ready" and all(
        result.get(key)
        for key in (
            "buildSuccess",
            "testSuccess",
            "processStartSuccess",
            "healthCheckSuccess",
            "acceptanceCheckSuccess",
            "previewAvailable",
        )
    )
    classification = None if preview_ready else _failure_classification(result)
    phase_paths = {
        "build_failure": ["provisioning", "source_staging", "static_analysis", "building"],
        "test_failure": ["provisioning", "source_staging", "static_analysis", "building", "testing"],
        "runtime_crash": ["provisioning", "source_staging", "static_analysis", "building", "testing", "starting"],
        "health_check_failure": ["provisioning", "source_staging", "static_analysis", "building", "testing", "starting", "health_checking"],
        "acceptance_test_failure": ["provisioning", "source_staging", "static_analysis", "building", "testing", "starting", "health_checking", "acceptance_testing"],
        "security_policy_violation": ["provisioning", "source_staging"],
        "resource_violation": ["provisioning", "source_staging", "static_analysis", "building"],
    }
    success_path = [
        "provisioning",
        "source_staging",
        "static_analysis",
        "building",
        "testing",
        "starting",
        "health_checking",
        "acceptance_testing",
    ]

    if not preview_ready:
        await _record_runner_failure_observation(db, row, state, result, classification)
    await _advance_runner_path(
        db,
        row,
        success_path if preview_ready else phase_paths.get(classification, []),
    )

    if preview_ready:
        await _event(db, row, "running", message="Generated application process is running")
        await _event(db, row, "preview_ready", message="Health and acceptance checks passed")
        preview = response["preview"]
        existing = await db.scalar(
            select(RunnerPreviewRecord).where(
                RunnerPreviewRecord.build_id == row.id,
                RunnerPreviewRecord.runner_preview_id == preview["id"],
            )
        )
        if not existing:
            db.add(
                RunnerPreviewRecord(
                    tenant_id=row.tenant_id,
                    build_id=row.id,
                    runner_preview_id=preview["id"],
                    target_url=preview["targetUrl"],
                    expires_at=datetime.utcnow()
                    + timedelta(seconds=submission.resources.previewSeconds),
                    created_by=row.created_by,
                )
            )
    else:
        failure_state = _failure_state_for(row.state, classification)
        try:
            await _event(
                db,
                row,
                failure_state,
                event_type="failure",
                message="Runner quality gate failed",
                details=_failure_evidence(result),
            )
            if classification:
                row.failure_classification = classification
        except RunnerStateError as error:
            # The original result was already checkpointed. Record the secondary
            # orchestration defect without replacing the primary runner evidence.
            await _event(
                db,
                row,
                row.state,
                event_type="runner_orchestration_error",
                message="Runner failure could not be normalized",
                details={
                    "classification": classification,
                    "targetState": failure_state,
                    "error": str(error),
                },
            )
            await db.commit()
            raise

    row.result_json = json.dumps(result)
    row.started_at = row.started_at or row.created_at
    row.completed_at = None if row.state == "preview_ready" else datetime.utcnow()
    existing_artifacts = await db.scalar(
        select(func.count(RunnerArtifactRecord.id)).where(RunnerArtifactRecord.build_id == row.id)
    )
    if not existing_artifacts:
        for artifact in result.get("artifacts", []):
            db.add(
                RunnerArtifactRecord(
                    tenant_id=row.tenant_id,
                    build_id=row.id,
                    kind=artifact.get("kind", "output"),
                    name=artifact.get("name", "artifact"),
                    digest=artifact.get("digest", "sha256:" + "0" * 64),
                    size_bytes=artifact.get("sizeBytes", 0),
                    reference=artifact.get("reference", "runner"),
                    metadata_json=json.dumps(artifact),
                )
            )
    await db.commit()
    await db.refresh(row)
    return row


async def owned_build(db, tenant_id, build_id):
    row = await db.get(RunnerBuildRecord, build_id)
    if not row or row.tenant_id != tenant_id:
        raise LookupError("Runner build not found")
    return row


async def refresh_build(db, row, adapter=None):
    if not row.runner_job_id or row.state in TERMINAL | {"preview_ready"}:
        return row
    adapter = adapter or ExternalRunnerAdapter()
    response = await adapter.status(row.runner_job_id)
    submission = BuildSubmission.model_validate_json(row.submission_json)
    return await apply_runner_response(db, row, response, submission)


async def build_events(db, row):
    return list(
        (
            await db.scalars(
                select(RunnerBuildEvent)
                .where(
                    RunnerBuildEvent.build_id == row.id,
                    RunnerBuildEvent.tenant_id == row.tenant_id,
                )
                .order_by(RunnerBuildEvent.sequence)
            )
        ).all()
    )


async def active_preview(db, tenant_id, preview_id):
    row = await db.get(RunnerPreviewRecord, preview_id)
    if (
        not row
        or row.tenant_id != tenant_id
        or row.state != "active"
        or row.expires_at <= datetime.utcnow()
    ):
        raise LookupError("Active preview not found")
    build = await owned_build(db, tenant_id, row.build_id)
    if build.state != "preview_ready":
        raise LookupError("Preview build is not running")
    return row, build


async def stop_preview(db, row, build, adapter):
    await adapter.stop_preview(row.runner_preview_id)
    row.state = "stopped"
    row.stopped_at = datetime.utcnow()
    await _event(db, build, "cleaning", message="Preview termination requested")
    await _event(db, build, "cleaned", message="Runner resources cleaned")
    build.completed_at = datetime.utcnow()
    await db.commit()


def build_json(row):
    submission = json.loads(row.submission_json)
    return {
        "id": row.id,
        "planId": row.plan_id,
        "sourceBundleId": row.source_bundle_id,
        "sourceVersion": submission.get("sourceVersion"),
        "runnerJobId": row.runner_job_id,
        "state": row.state,
        "runnerImplementation": row.runner_implementation,
        "isolationProfile": row.isolation_profile,
        "attempt": row.attempt,
        "failureClassification": row.failure_classification,
        "resourcePolicy": submission.get("resources"),
        "networkPolicy": submission.get("network"),
        "operations": submission.get("operations", []),
        "result": json.loads(row.result_json),
    }
