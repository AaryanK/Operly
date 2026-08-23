"""Bridge immutable coding-harness source bundles into an isolated runner."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select

from packages.coding_harness.runtime_resolution import RuntimeResolutionError, validate_runtime_contract
from packages.custom_software.runner_adapters import ExternalRunnerAdapter, RunnerAdapter
from packages.custom_software.runner_contracts import BuildSubmission
from packages.custom_software.runner_service import _event, apply_runner_response
from packages.custom_software.source_bundles import SourceFile, build_bundle
from packages.database.custom_software_models import GeneratedSourceBundle, RunnerBuildRecord
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
        # Communication errors are recorded by the normal submit path with a
        # durable build record. Do not turn network availability into a source error.
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
    existing = await db.scalar(
        select(RunnerBuildRecord).where(
            RunnerBuildRecord.tenant_id == tenant_id,
            RunnerBuildRecord.idempotency_key == idempotency_key,
        )
    )
    if existing:
        return existing
    if source.tenant_id != tenant_id or source.plan_id != plan_row.id:
        raise SourceRecordError("Source bundle does not belong to this approved plan")
    if source.plan_version != plan_row.approved_version:
        raise SourceRecordError("Source bundle is not based on the approved plan version")

    bundle = source_bundle_from_record(source)
    submission = _submission_for_source(source, bundle, idempotency_key)
    adapter = adapter or ExternalRunnerAdapter()
    await _check_runner_profile(adapter, submission.stackId, submission.stackVersion)

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

    try:
        response = await adapter.submit(submission, bundle)
    except Exception as error:
        await _event(
            db,
            row,
            "failed",
            event_type="runner_unavailable",
            message="runner_unavailable",
            details={
                "message": str(error),
                "runtime": submission.stackId,
                "runtimeVersion": submission.stackVersion,
            },
        )
        row.result_json = json.dumps(
            {
                "code": "runner_unavailable",
                "runtime": submission.stackId,
                "runtimeVersion": submission.stackVersion,
                "failureEvidence": {
                    "classification": "runner_unavailable",
                    "message": str(error),
                },
            }
        )
        row.completed_at = datetime.utcnow()
        await db.commit()
        await db.refresh(row)
        return row

    return await apply_runner_response(db, row, response, submission)
