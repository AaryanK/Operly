"""Bridge immutable coding-harness source bundles into an isolated runner."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select

from packages.runtime_plugins.runtime_resolution import RuntimeResolutionError, validate_runtime_contract
from packages.runtime_plugins.runner_adapters import ExternalRunnerAdapter, RunnerAdapter
from packages.runtime_plugins.runner_contracts import BuildSubmission
from packages.runtime_plugins.runner_service import _event, apply_runner_response
from packages.software_projects.source_bundle import SourceFile, build_bundle
from packages.database.custom_software_models import GeneratedSourceBundle, RunnerBuildRecord
from packages.relational_data.bindings import RelationalBindingUnavailable, attach_transport_grants
from packages.runtime_plugins import register_builtin_runtimes


class SourceRecordError(ValueError):
    pass


class RunnerProfileUnsupported(SourceRecordError):
    def __init__(
        self,
        profile_id: str,
        supported: list[str],
        *,
        required_version: int | None = None,
        advertised_version: int | None = None,
    ):
        self.profile_id = profile_id
        self.supported = supported
        self.required_version = required_version
        self.advertised_version = advertised_version
        if advertised_version is not None and required_version is not None:
            detail = (
                f"Runner advertises {profile_id} profileVersion={advertised_version}, "
                f"but Operly requires profileVersion={required_version}"
            )
        else:
            detail = (
                f"Runner does not support source runtime {profile_id}; supported profiles: "
                f"{', '.join(supported) or 'none'}"
            )
        super().__init__(detail)


def source_bundle_from_record(source: GeneratedSourceBundle):
    try:
        manifest = json.loads(source.manifest_json)
        rows = json.loads(source.files_json)
        files = [
            SourceFile(
                str(item["path"]),
                str(item["content"]).encode("utf-8"),
                str(item.get("generatedBy") or "coding_harness"),
            )
            for item in rows
        ]
        rebuilt = build_bundle(
            files,
            source.tenant_id,
            source.application_id,
            source.plan_id,
            source.plan_version,
            source.source_version,
            str(manifest["promptDigest"]),
        )
    except Exception as error:
        raise SourceRecordError("Stored source bundle is invalid") from error
    if rebuilt.digest != source.bundle_digest:
        raise SourceRecordError("Stored source bundle digest does not match its contents")
    return rebuilt


def _submission_for_source(
    source: GeneratedSourceBundle,
    bundle,
    idempotency_key: str,
) -> BuildSubmission:
    try:
        profile_id = validate_runtime_contract(bundle)
        runtime = register_builtin_runtimes().get(profile_id)
        builder = getattr(runtime, "build_submission_from_record", None)
        if builder is None:
            raise ValueError(
                f"Runtime plugin {profile_id} does not support legacy source-record builds"
            )
        return builder(source, bundle, idempotency_key)
    except (RuntimeResolutionError, LookupError, ValueError) as error:
        raise SourceRecordError(str(error)) from error


async def _check_runner_profile(
    adapter: RunnerAdapter,
    profile_id: str,
    profile_version: int,
) -> None:
    try:
        capabilities = await adapter.capabilities()
    except Exception:
        return
    if not capabilities:
        return
    profiles = capabilities.get("profiles") or {}
    supported = sorted(str(key) for key in profiles)
    if profile_id not in profiles:
        raise RunnerProfileUnsupported(
            profile_id,
            supported,
            required_version=profile_version,
        )
    advertised = profiles.get(profile_id) or {}
    try:
        advertised_version = int(advertised.get("profileVersion", 0))
    except (TypeError, ValueError):
        advertised_version = 0
    if advertised_version != int(profile_version):
        raise RunnerProfileUnsupported(
            profile_id,
            supported,
            required_version=int(profile_version),
            advertised_version=advertised_version,
        )


async def _record_profile_mismatch(db, row, submission: BuildSubmission, error: RunnerProfileUnsupported):
    await _event(
        db,
        row,
        "failed",
        event_type="runner_profile_unsupported",
        message="runner_profile_unsupported",
        details={
            "message": str(error),
            "runtime": submission.stackId,
            "runtimeVersion": submission.stackVersion,
            "supportedProfiles": ",".join(error.supported),
            "advertisedVersion": error.advertised_version,
        },
    )
    row.failure_classification = "runner_profile_unsupported"
    row.result_json = json.dumps(
        {
            "code": "runner_profile_unsupported",
            "runtime": submission.stackId,
            "runtimeVersion": submission.stackVersion,
            "supportedProfiles": error.supported,
            "advertisedVersion": error.advertised_version,
            "failureEvidence": {
                "classification": "runner_profile_unsupported",
                "message": str(error),
            },
        }
    )
    row.completed_at = datetime.utcnow()
    await db.commit()
    await db.refresh(row)


async def _record_binding_unavailable(db, row, submission: BuildSubmission, error: Exception):
    await _event(
        db,
        row,
        "failed",
        event_type="service_binding_unavailable",
        message="service_binding_unavailable",
        details={
            "message": str(error),
            "runtime": submission.stackId,
            "applicationId": submission.applicationId,
        },
    )
    row.failure_classification = "service_binding_unavailable"
    row.result_json = json.dumps(
        {
            "code": "service_binding_unavailable",
            "failureEvidence": {
                "classification": "service_binding_unavailable",
                "message": str(error),
            },
        }
    )
    row.completed_at = datetime.utcnow()
    await db.commit()
    await db.refresh(row)


def _classify_runner_submit_error(error: Exception) -> dict:
    status = getattr(error, "status", None)
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    response = getattr(error, "response_body", None)
    detail = ""
    if isinstance(response, dict):
        detail = str(response.get("error") or response.get("detail") or "").strip()
    message = detail or str(error) or type(error).__name__
    message = " ".join(message.split())[:1000]
    if status is not None and 400 <= status < 500 and status not in {408, 409, 425, 429}:
        classification = "runner_submission_rejected"
        retryable = False
    else:
        classification = "runner_unavailable"
        retryable = True
    return {
        "classification": classification,
        "retryable": retryable,
        "status": status,
        "message": message,
    }


async def _record_runner_submit_error(db, row, submission: BuildSubmission, error: Exception):
    evidence = _classify_runner_submit_error(error)
    classification = evidence["classification"]
    details = {
        "message": evidence["message"],
        "runtime": submission.stackId,
        "runtimeVersion": submission.stackVersion,
        "runnerStatus": evidence["status"],
        "retryable": evidence["retryable"],
    }
    await _event(
        db,
        row,
        "failed",
        event_type=classification,
        message=classification,
        details=details,
    )
    row.failure_classification = classification
    row.result_json = json.dumps(
        {
            "code": classification,
            "runtime": submission.stackId,
            "runtimeVersion": submission.stackVersion,
            "runnerStatus": evidence["status"],
            "failureEvidence": {
                "classification": classification,
                "message": evidence["message"],
                "runnerStatus": evidence["status"],
                "retryable": evidence["retryable"],
            },
        }
    )
    row.completed_at = datetime.utcnow()
    await db.commit()
    await db.refresh(row)


async def _submit_persisted_build(
    db,
    row: RunnerBuildRecord,
    submission: BuildSubmission,
    bundle,
    adapter: RunnerAdapter,
):
    """Submit or re-submit one persisted build using runner idempotency.

    A worker can die after the remote runner accepts the request but before the
    response (including runner job id) is committed locally. In that ambiguous
    window, a local ``queued`` row with no ``runner_job_id`` is not evidence that
    submission never happened. Re-sending the exact durable idempotency key is
    the only safe recovery operation: the runner returns the existing remote job
    if it already accepted it, or creates it if the first request never arrived.
    """
    try:
        await _check_runner_profile(adapter, submission.stackId, submission.stackVersion)
    except RunnerProfileUnsupported as error:
        await _record_profile_mismatch(db, row, submission, error)
        raise

    try:
        transport_submission = attach_transport_grants(submission)
    except RelationalBindingUnavailable as error:
        await _record_binding_unavailable(db, row, submission, error)
        return row

    try:
        response = await adapter.submit(transport_submission, bundle)
    except Exception as error:
        await _record_runner_submit_error(db, row, submission, error)
        return row

    return await apply_runner_response(db, row, response, submission)


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
    if source.tenant_id != tenant_id or source.plan_id != plan_row.id:
        raise SourceRecordError("Source bundle does not belong to this approved plan")
    if source.plan_version != plan_row.approved_version:
        raise SourceRecordError("Source bundle is not based on the approved plan version")

    adapter = adapter or ExternalRunnerAdapter()
    existing = await db.scalar(
        select(RunnerBuildRecord).where(
            RunnerBuildRecord.tenant_id == tenant_id,
            RunnerBuildRecord.idempotency_key == idempotency_key,
        )
    )
    if existing:
        if existing.plan_id != plan_row.id or existing.source_bundle_id != source.id:
            raise SourceRecordError("Build idempotency key is already bound to different immutable source")
        if existing.runner_job_id or existing.state not in {"created", "queued"}:
            return existing
        try:
            submission = BuildSubmission.model_validate_json(existing.submission_json)
        except Exception as error:
            raise SourceRecordError("Persisted build submission is invalid") from error
        if submission.idempotencyKey != idempotency_key:
            raise SourceRecordError("Persisted build submission idempotency key is inconsistent")
        bundle = source_bundle_from_record(source)
        if submission.sourceBundleDigest != bundle.digest:
            raise SourceRecordError("Persisted build submission no longer matches immutable source")
        return await _submit_persisted_build(db, existing, submission, bundle, adapter)

    bundle = source_bundle_from_record(source)
    submission = _submission_for_source(source, bundle, idempotency_key)

    # Persist only the semantic, credential-free submission. Short-lived transport
    # grants are attached later and exist only while crossing the trusted runner boundary.
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
    await _event(
        db,
        row,
        "created",
        message=f"Coding harness build record created for attempt {row.attempt}",
    )
    await _event(
        db,
        row,
        "queued",
        message=(
            f"Harness-authored source submitted with runtime plugin "
            f"{submission.stackId}@{submission.stackVersion}"
        ),
    )
    await db.commit()

    return await _submit_persisted_build(db, row, submission, bundle, adapter)