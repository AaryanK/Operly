from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.database.plugin_platform_models import (
    PluginInstallationRecord,
    PluginRuntimeInstanceRecord,
    PluginVersionRecord,
)
from packages.plugins.jobs import digital_platform_jobs


router = APIRouter(prefix="/api/plugin-platform", tags=["plugin-platform-runtime"])


class ReconcileRuntimeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str | None = Field(default=None, max_length=1800)


def _owner(auth: AuthContext) -> None:
    if auth.role != "owner":
        raise HTTPException(
            status_code=403,
            detail="Only Workspace owners can reconcile plugin runtimes",
        )


async def _installation(
    db: AsyncSession,
    *,
    tenant_id: str,
    installation_id: str,
) -> PluginInstallationRecord:
    row = await db.scalar(
        select(PluginInstallationRecord).where(
            PluginInstallationRecord.id == installation_id,
            PluginInstallationRecord.tenant_id == tenant_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Plugin installation not found")
    return row


def _safe_json(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


@router.get("/installations/{installation_id}/runtime")
async def runtime_status(
    installation_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _owner(auth)
    installation = await _installation(
        db, tenant_id=auth.tenant.id, installation_id=installation_id
    )
    version = await db.get(PluginVersionRecord, installation.version_id)
    rows = list(
        (
            await db.scalars(
                select(PluginRuntimeInstanceRecord)
                .where(
                    PluginRuntimeInstanceRecord.tenant_id == auth.tenant.id,
                    PluginRuntimeInstanceRecord.installation_id == installation_id,
                )
                .order_by(PluginRuntimeInstanceRecord.updated_at.desc())
            )
        ).all()
    )
    return {
        "installation_id": installation.id,
        "installation_status": installation.status,
        "installation_enabled": installation.enabled,
        "version_id": installation.version_id,
        "validation_status": version.validation_status if version else "missing",
        "instances": [
            {
                "id": row.id,
                "version_id": row.version_id,
                "runtime_profile": row.runtime_profile,
                "runtime_kind": row.runtime_kind,
                "state": row.state,
                "provider": row.provider,
                "provider_reference": row.provider_reference,
                "endpoint_reference": row.endpoint_reference,
                "artifact_id": row.artifact_id,
                "health_state": row.health_state,
                "health_evidence": _safe_json(row.health_evidence_json),
                "last_heartbeat_at": (
                    row.last_heartbeat_at.isoformat() if row.last_heartbeat_at else None
                ),
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in rows
        ],
    }


@router.post("/installations/{installation_id}/runtime/reconcile", status_code=202)
async def reconcile_runtime(
    installation_id: str,
    payload: ReconcileRuntimeInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _owner(auth)
    installation = await _installation(
        db, tenant_id=auth.tenant.id, installation_id=installation_id
    )
    version = await db.get(PluginVersionRecord, installation.version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Plugin version not found")
    endpoint = str(payload.endpoint or "").strip() or None
    endpoint_digest = hashlib.sha256((endpoint or "existing").encode("utf-8")).hexdigest()[:24]
    try:
        job = await digital_platform_jobs.enqueue(
            db,
            tenant_id=auth.tenant.id,
            job_type="plugin.runtime.reconcile",
            subject_kind="plugin_installation",
            subject_id=installation.id,
            idempotency_key=(
                f"plugin.runtime.reconcile:{installation.id}:{version.id}:"
                f"{version.manifest_digest}:{endpoint_digest}"
            ),
            payload={
                "installation_id": installation.id,
                "version_id": version.id,
                "manifest_digest": version.manifest_digest,
                "endpoint": endpoint,
            },
            priority=70,
            max_attempts=8,
            created_by=auth.user.id,
        )
        await db.commit()
    except ValueError as error:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "accepted": True,
        "job_id": job.id,
        "job_state": job.state,
        "installation_id": installation.id,
        "validation_status": version.validation_status,
        "direct_health_override_allowed": False,
    }
