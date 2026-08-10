"""Bridge immutable coding-harness source bundles into an isolated runner."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select

from packages.coding_harness.runtime_resolution import RuntimeResolutionError, validate_runtime_contract
from packages.custom_software.runner_adapters import ExternalRunnerAdapter, RunnerAdapter
from packages.custom_software.runner_contracts import BuildSubmission, HealthCheck, NetworkPolicy, ResourcePolicy
from packages.custom_software.runner_service import _event, apply_runner_response
from packages.custom_software.runtime_profiles import runtime_profile
from packages.custom_software.source_bundles import SourceFile, build_bundle
from packages.database.custom_software_models import GeneratedSourceBundle, RunnerBuildRecord


class SourceRecordError(ValueError):
    pass


class RunnerProfileUnsupported(SourceRecordError):
    def __init__(self, profile_id: str, supported: list[str]):
        self.profile_id = profile_id
        self.supported = supported
        super().__init__(f"Runner does not support source runtime {profile_id}; supported profiles: {', '.join(supported) or 'none'}")


def source_bundle_from_record(source: GeneratedSourceBundle):
    try:
        manifest = json.loads(source.manifest_json)
        rows = json.loads(source.files_json)
        files = [SourceFile(str(item["path"]), str(item["content"]).encode("utf-8"), str(item.get("generatedBy") or "coding_harness")) for item in rows]
        rebuilt = build_bundle(files, source.tenant_id, source.application_id, source.plan_id, source.plan_version, source.source_version, str(manifest["promptDigest"]))
    except Exception as error:
        raise SourceRecordError("Stored source bundle is invalid") from error
    if rebuilt.digest != source.bundle_digest:
        raise SourceRecordError("Stored source bundle digest does not match its contents")
    return rebuilt


def _submission_for_source(source: GeneratedSourceBundle, bundle, idempotency_key: str) -> BuildSubmission:
    try:
        profile_id = validate_runtime_contract(bundle)
        profile = runtime_profile(profile_id)
    except (RuntimeResolutionError, ValueError) as error:
        raise SourceRecordError(str(error)) from error
    resources = ResourcePolicy.model_validate(profile["resources"])
    return BuildSubmission(
        workspaceId=source.tenant_id,
        applicationId=source.application_id,
        planVersion=source.plan_version,
        sourceVersion=source.source_version,
        stackId=profile_id,
        sourceBundleDigest=source.bundle_digest,
        operations=profile["operations"],
        healthCheck=HealthCheck.model_validate(profile["health"]),
        resources=resources,
        network=NetworkPolicy(mode="none"),
        requiredPorts=profile["ports"],
        artifactPaths=profile["artifactPaths"],
        maxDurationSeconds=resources.durationSeconds,
        idempotencyKey=idempotency_key,
    )


async def _check_runner_profile(adapter: RunnerAdapter, profile_id: str) -> None:
    try:
        capabilities = await adapter.capabilities()
    except Exception:
        # Communication errors are recorded by the normal submit path with a
        # durable build record. Do not turn network availability into a source error.
        return
    if not capabilities:
        return
    profiles = capabilities.get("profiles") or {}
    supported = sorted(str(key) for key in profiles)
    if profile_id not in profiles:
        raise RunnerProfileUnsupported(profile_id, supported)


async def submit_source_build(
    db,
    tenant_id: str,
    user_id: str,
    plan_row,
    plan,
    source: GeneratedSourceBundle,
    idempotency_key: str,
    adapter: RunnerAdapter | None = None,
    attempt: int = 1,
):
    existing = await db.scalar(select(RunnerBuildRecord).where(RunnerBuildRecord.tenant_id == tenant_id, RunnerBuildRecord.idempotency_key == idempotency_key))
    if existing:
        return existing
    if source.tenant_id != tenant_id or source.plan_id != plan_row.id:
        raise SourceRecordError("Source bundle does not belong to this approved plan")
    if source.plan_version != plan_row.approved_version:
        raise SourceRecordError("Source bundle is not based on the approved plan version")

    bundle = source_bundle_from_record(source)
    submission = _submission_for_source(source, bundle, idempotency_key)
    adapter = adapter or ExternalRunnerAdapter()
    await _check_runner_profile(adapter, submission.stackId)

    row = RunnerBuildRecord(
        tenant_id=tenant_id,
        plan_id=plan_row.id,
        source_bundle_id=source.id,
        idempotency_key=idempotency_key,
        state="created",
        runner_implementation=adapter.implementation,
        isolation_profile=adapter.isolation_profile,
        submission_json=submission.model_dump_json(),
        attempt=max(1, int(attempt)),
        created_by=user_id,
    )
    db.add(row)
    await db.flush()
    await _event(db, row, "created", message=f"Coding harness build record created for attempt {row.attempt}")
    await _event(db, row, "queued", message=f"Harness-authored source submitted with negotiated runtime {submission.stackId}")
    await db.commit()

    try:
        response = await adapter.submit(submission, bundle)
    except Exception as error:
        await _event(db, row, "failed", event_type="runner_unavailable", message="runner_unavailable", details={"message": str(error), "runtime": submission.stackId})
        row.result_json = json.dumps({"code": "runner_unavailable", "runtime": submission.stackId, "failureEvidence": {"classification": "runner_unavailable", "message": str(error)}})
        row.completed_at = datetime.utcnow()
        await db.commit()
        await db.refresh(row)
        return row

    return await apply_runner_response(db, row, response, submission)
