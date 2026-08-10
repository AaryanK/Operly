"""Bridge immutable coding-harness source bundles into the existing isolated runner."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select

from packages.custom_software.runner_adapters import ExternalRunnerAdapter, RunnerAdapter
from packages.custom_software.runner_service import _event, _submission, apply_runner_response
from packages.custom_software.source_bundles import SourceFile, build_bundle
from packages.database.custom_software_models import GeneratedSourceBundle, RunnerBuildRecord


class SourceRecordError(ValueError):
    pass


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


async def submit_source_build(
    db,
    tenant_id: str,
    user_id: str,
    plan_row,
    plan,
    source: GeneratedSourceBundle,
    idempotency_key: str,
    adapter: RunnerAdapter | None = None,
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
    adapter = adapter or ExternalRunnerAdapter()
    try:
        submission = _submission(source, plan, idempotency_key)
    except ValueError as error:
        runtime = getattr(getattr(plan, "stack", None), "runtime", None) or "unknown"
        raise SourceRecordError(
            f"Approved runtime '{runtime}' does not yet have an isolated runner profile"
        ) from error
    row = RunnerBuildRecord(
        tenant_id=tenant_id,
        plan_id=plan_row.id,
        source_bundle_id=source.id,
        idempotency_key=idempotency_key,
        state="created",
        runner_implementation=adapter.implementation,
        isolation_profile=adapter.isolation_profile,
        submission_json=submission.model_dump_json(),
        attempt=1,
        created_by=user_id,
    )
    db.add(row)
    await db.flush()
    await _event(db, row, "created", message="Coding harness build record created")
    await _event(db, row, "queued", message="Harness-authored source submitted to isolated runner")
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
            details={"message": str(error)},
        )
        row.result_json = json.dumps({"code": "runner_unavailable"})
        row.completed_at = datetime.utcnow()
        await db.commit()
        await db.refresh(row)
        return row

    return await apply_runner_response(db, row, response, submission)
