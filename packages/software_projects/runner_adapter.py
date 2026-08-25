"""Temporary bridge between canonical SoftwareProject source and the mature runner.

RunnerBuildRecord still references GeneratedSourceBundle.  Until that persistence is
migrated, this adapter projects canonical source into an internal generated bundle
only for build/test execution.  It never becomes project identity or source truth.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import func, select

from packages.database.custom_software_models import GeneratedSourceBundle
from packages.database.software_project_models import SoftwareSourceVersionRecord
from packages.software_projects.source_service import files_from_row


async def materialize_runner_bundle(
    db,
    *,
    tenant_id: str,
    user_id: str,
    source: SoftwareSourceVersionRecord,
    plan_id: str,
    plan_version: int,
) -> GeneratedSourceBundle:
    provenance = {
        "harness": "canonical_software_source_adapter_v1",
        "sourceOperation": "runner_projection",
        "canonicalSoftwareProjectId": source.project_id,
        "canonicalSourceVersionId": source.id,
        "canonicalSourceVersion": source.source_version,
        "detectedRuntimeProfile": source.runtime_profile,
        "secretValuesStored": False,
        "projectionOnly": True,
    }
    application_id = f"software-project-{source.project_id}"
    version = int(
        await db.scalar(
            select(func.max(GeneratedSourceBundle.source_version)).where(
                GeneratedSourceBundle.tenant_id == tenant_id,
                GeneratedSourceBundle.application_id == application_id,
            )
        )
        or 0
    ) + 1
    files = files_from_row(source)
    records = [
        {"path": path, "content": content, "generatedBy": "canonical_software_source"}
        for path, content in sorted(files.items())
    ]
    manifest = json.loads(source.manifest_json or "{}")
    manifest.update(
        {
            "workspaceId": tenant_id,
            "applicationId": application_id,
            "planId": plan_id,
            "planVersion": int(plan_version),
            "sourceVersion": version,
            "canonicalSourceVersionId": source.id,
        }
    )
    digest_input = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    row = GeneratedSourceBundle(
        tenant_id=tenant_id,
        plan_id=plan_id,
        plan_version=int(plan_version),
        source_version=version,
        application_id=application_id,
        bundle_digest="sha256:" + hashlib.sha256(digest_input).hexdigest(),
        manifest_json=json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        files_json=json.dumps(records, ensure_ascii=False, sort_keys=True),
        provenance_json=json.dumps(provenance, ensure_ascii=False, sort_keys=True),
        created_by=user_id,
    )
    db.add(row)
    await db.flush()
    return row
