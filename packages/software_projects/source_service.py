"""Canonical immutable source persistence for SoftwareProject."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from sqlalchemy import func, select

from packages.database.software_project_models import SoftwareProjectRecord, SoftwareSourceVersionRecord
from packages.software_projects.source_bundle import MAX_BYTES, MAX_FILES, normalized_path


class SoftwareSourceError(ValueError):
    pass


def _json(value: str | None, default):
    try:
        parsed = json.loads(value or "")
    except Exception:
        return default
    return parsed


def files_from_row(row: SoftwareSourceVersionRecord) -> dict[str, str]:
    records = _json(row.files_json, [])
    if not isinstance(records, list):
        raise SoftwareSourceError("Stored software source is invalid")
    result: dict[str, str] = {}
    for item in records:
        if not isinstance(item, dict):
            raise SoftwareSourceError("Stored software source is invalid")
        path = normalized_path(str(item.get("path") or ""))
        if path in result:
            raise SoftwareSourceError("Stored software source contains duplicate paths")
        result[path] = str(item.get("content") or "")
    return result


def source_json(row: SoftwareSourceVersionRecord) -> dict[str, Any]:
    manifest = _json(row.manifest_json, {})
    provenance = _json(row.provenance_json, {})
    return {
        "id": row.id,
        "project_id": row.project_id,
        "source_version": row.source_version,
        "parent_source_id": row.parent_source_id,
        "runtime_profile": row.runtime_profile,
        "bundle_digest": row.bundle_digest,
        "manifest": manifest if isinstance(manifest, dict) else {},
        "files": files_from_row(row),
        "provenance": provenance if isinstance(provenance, dict) else {},
        "change_summary": row.change_summary,
        "originating_run_id": row.originating_run_id,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat(),
    }


def _canonical_records(files: Mapping[str, str]) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    if len(files) > MAX_FILES:
        raise SoftwareSourceError("Software source file-count limit exceeded")
    total = 0
    rows: list[dict[str, Any]] = []
    manifest_rows: list[dict[str, Any]] = []
    for raw_path, raw_content in files.items():
        path = normalized_path(str(raw_path))
        content = str(raw_content)
        payload = content.encode("utf-8")
        total += len(payload)
        if total > MAX_BYTES:
            raise SoftwareSourceError("Software source size limit exceeded")
        if b"BEGIN PRIVATE KEY" in payload or b"OPERLY_SANDBOX_RUNNER_TOKEN" in payload:
            raise SoftwareSourceError("Secrets are forbidden in software source")
        digest = hashlib.sha256(payload).hexdigest()
        rows.append({"path": path, "content": content})
        manifest_rows.append({"path": path, "bytes": len(payload), "digest": f"sha256:{digest}"})
    rows.sort(key=lambda item: item["path"])
    manifest_rows.sort(key=lambda item: item["path"])
    manifest = {"schemaVersion": 1, "files": manifest_rows, "totalBytes": total}
    digest_input = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return rows, manifest, f"sha256:{hashlib.sha256(digest_input).hexdigest()}"


class SoftwareSourceService:
    async def latest(self, db, tenant_id: str, project_id: str) -> SoftwareSourceVersionRecord | None:
        return await db.scalar(
            select(SoftwareSourceVersionRecord)
            .where(
                SoftwareSourceVersionRecord.tenant_id == tenant_id,
                SoftwareSourceVersionRecord.project_id == project_id,
            )
            .order_by(SoftwareSourceVersionRecord.source_version.desc())
            .limit(1)
        )

    async def get(self, db, tenant_id: str, project_id: str, source_id: str) -> SoftwareSourceVersionRecord:
        row = await db.scalar(
            select(SoftwareSourceVersionRecord).where(
                SoftwareSourceVersionRecord.id == source_id,
                SoftwareSourceVersionRecord.tenant_id == tenant_id,
                SoftwareSourceVersionRecord.project_id == project_id,
            )
        )
        if row is None:
            raise LookupError("Software source version not found")
        return row

    async def persist(
        self,
        db,
        *,
        tenant_id: str,
        project_id: str,
        user_id: str,
        files: Mapping[str, str],
        runtime_profile: str,
        provenance: Mapping[str, Any] | None = None,
        change_summary: str = "",
        originating_run_id: str | None = None,
        parent_source_id: str | None = None,
    ) -> SoftwareSourceVersionRecord:
        project = await db.scalar(
            select(SoftwareProjectRecord).where(
                SoftwareProjectRecord.id == project_id,
                SoftwareProjectRecord.tenant_id == tenant_id,
            )
        )
        if project is None:
            raise LookupError("Software project not found")

        records, manifest, digest = _canonical_records(files)
        current = await self.latest(db, tenant_id, project_id)
        if current is not None and current.bundle_digest == digest:
            return current
        source_version = int(
            await db.scalar(
                select(func.max(SoftwareSourceVersionRecord.source_version)).where(
                    SoftwareSourceVersionRecord.project_id == project_id
                )
            )
            or 0
        ) + 1
        row = SoftwareSourceVersionRecord(
            tenant_id=tenant_id,
            project_id=project_id,
            source_version=source_version,
            parent_source_id=parent_source_id or (current.id if current else None),
            runtime_profile=str(runtime_profile or "unknown")[:160],
            bundle_digest=digest,
            manifest_json=json.dumps(manifest, sort_keys=True, separators=(",", ":")),
            files_json=json.dumps(records, ensure_ascii=False, sort_keys=True),
            provenance_json=json.dumps(dict(provenance or {}), ensure_ascii=False, sort_keys=True, default=str),
            change_summary=str(change_summary or "")[:2000],
            originating_run_id=str(originating_run_id or "")[:160] or None,
            created_by=str(user_id or "")[:120],
        )
        db.add(row)
        await db.flush()
        project.active_source_version_id = row.id
        project.active_runtime_id = row.runtime_profile
        await db.flush()
        return row

    async def import_runner_source(
        self,
        db,
        *,
        tenant_id: str,
        project_id: str,
        source,
        originating_run_id: str | None = None,
    ) -> SoftwareSourceVersionRecord:
        """Import a verified runner adapter bundle into canonical source authority."""
        records = _json(getattr(source, "files_json", None), [])
        files = {
            str(item.get("path") or ""): str(item.get("content") or "")
            for item in records
            if isinstance(item, dict) and item.get("path")
        }
        provenance = _json(getattr(source, "provenance_json", None), {})
        runtime = str((provenance if isinstance(provenance, dict) else {}).get("detectedRuntimeProfile") or "generated-runtime")
        return await self.persist(
            db,
            tenant_id=tenant_id,
            project_id=project_id,
            user_id=str(getattr(source, "created_by", "") or ""),
            files=files,
            runtime_profile=runtime,
            provenance=provenance if isinstance(provenance, dict) else {},
            change_summary=str((provenance if isinstance(provenance, dict) else {}).get("summary") or "Imported verified runner source"),
            originating_run_id=originating_run_id,
        )
