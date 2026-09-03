from __future__ import annotations

import json
import mimetypes
import stat
import zipfile
from io import BytesIO
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db
from packages.artifacts import ArtifactScope, ArtifactService
from packages.database.models import Tenant
from packages.database.plugin_platform_models import (
    PluginInstallationRecord,
    PluginPackageRecord,
    PluginRuntimeInstanceRecord,
    PluginVersionRecord,
)
from packages.plugins.contracts import PluginManifest


public_router = APIRouter(prefix="/api/public/plugins", tags=["plugin-hosting-public"])
MAX_HOSTED_ASSET_BYTES = 5 * 1024 * 1024


def _safe_json(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _asset_path(value: str | None) -> str:
    clean = str(value or "").strip().replace("\\", "/").lstrip("/") or "index.html"
    path = PurePosixPath(clean)
    if path.is_absolute() or ".." in path.parts or any(not part or part == "." for part in path.parts):
        raise HTTPException(status_code=404, detail="Hosted plugin asset not found")
    return path.as_posix()


async def _hosted_context(
    db: AsyncSession,
    *,
    workspace_id: str,
    plugin_id: str,
) -> tuple[Tenant, PluginPackageRecord, PluginInstallationRecord, PluginVersionRecord, PluginRuntimeInstanceRecord, str]:
    tenant = await db.get(Tenant, workspace_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Hosted Workspace not found")
    package = await db.scalar(
        select(PluginPackageRecord).where(
            PluginPackageRecord.owner_tenant_id == tenant.id,
            PluginPackageRecord.plugin_id == plugin_id,
        )
    )
    if package is None:
        raise HTTPException(status_code=404, detail="Hosted plugin not found")
    installation = await db.scalar(
        select(PluginInstallationRecord).where(
            PluginInstallationRecord.tenant_id == tenant.id,
            PluginInstallationRecord.package_id == package.id,
            PluginInstallationRecord.status == "active",
            PluginInstallationRecord.enabled.is_(True),
        )
    )
    if installation is None:
        raise HTTPException(status_code=404, detail="Hosted plugin is not active")
    version = await db.get(PluginVersionRecord, installation.version_id)
    if version is None or version.validation_status != "passed":
        raise HTTPException(status_code=503, detail="Hosted plugin is not validated")
    try:
        manifest = PluginManifest.from_dict(json.loads(version.manifest_json))
    except Exception as error:
        raise HTTPException(status_code=503, detail="Hosted plugin manifest is invalid") from error
    if manifest.runtime is None:
        raise HTTPException(status_code=503, detail="Hosted plugin runtime is unavailable")
    instance = await db.scalar(
        select(PluginRuntimeInstanceRecord)
        .where(
            PluginRuntimeInstanceRecord.tenant_id == tenant.id,
            PluginRuntimeInstanceRecord.installation_id == installation.id,
            PluginRuntimeInstanceRecord.version_id == version.id,
            PluginRuntimeInstanceRecord.state.in_(["ready", "running"]),
            PluginRuntimeInstanceRecord.health_state == "healthy",
        )
        .order_by(PluginRuntimeInstanceRecord.updated_at.desc())
    )
    if instance is None:
        raise HTTPException(status_code=503, detail="Hosted plugin runtime is not ready")
    report = _safe_json(version.validation_report_json)
    artifact_id = str(report.get("validated_artifact_id") or "").strip()
    if not artifact_id:
        raise HTTPException(status_code=503, detail="Hosted plugin artifact is unavailable")
    return tenant, package, installation, version, instance, artifact_id


async def _serve(
    workspace_id: str,
    plugin_id: str,
    asset_path: str | None,
    db: AsyncSession,
) -> Response:
    tenant, package, _, version, instance, artifact_id = await _hosted_context(
        db,
        workspace_id=workspace_id,
        plugin_id=plugin_id,
    )
    scope = ArtifactScope("workspace", tenant.id, tenant_id=tenant.id)
    artifacts = ArtifactService(db)
    artifact = await artifacts.assert_workspace_artifact(tenant_id=tenant.id, artifact_id=artifact_id)
    raw = await artifacts.read_bytes(scope, artifact.id)
    target = _asset_path(asset_path)
    try:
        with zipfile.ZipFile(BytesIO(raw), "r") as archive:
            try:
                info = archive.getinfo(target)
            except KeyError as error:
                raise HTTPException(status_code=404, detail="Hosted plugin asset not found") from error
            mode = (info.external_attr >> 16) & 0o170000
            if info.is_dir() or mode == stat.S_IFLNK or info.file_size > MAX_HOSTED_ASSET_BYTES:
                raise HTTPException(status_code=404, detail="Hosted plugin asset not found")
            body = archive.read(info)
    except zipfile.BadZipFile as error:
        raise HTTPException(status_code=503, detail="Hosted plugin artifact is invalid") from error
    media_type = mimetypes.guess_type(target)[0] or "application/octet-stream"
    return Response(
        body,
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=60" if target != "index.html" else "no-store",
            "X-Operly-Workspace-Id": tenant.id,
            "X-Operly-Plugin-Id": package.plugin_id,
            "X-Operly-Plugin-Version": version.version,
            "X-Operly-Runtime-Provider": str(instance.provider or "unknown"),
        },
    )


@public_router.get("/{workspace_id}/{plugin_id}")
async def hosted_plugin_root(
    workspace_id: str,
    plugin_id: str,
    db: AsyncSession = Depends(get_db),
):
    return await _serve(workspace_id, plugin_id, "index.html", db)


@public_router.get("/{workspace_id}/{plugin_id}/{asset_path:path}")
async def hosted_plugin_asset(
    workspace_id: str,
    plugin_id: str,
    asset_path: str,
    db: AsyncSession = Depends(get_db),
):
    return await _serve(workspace_id, plugin_id, asset_path, db)
