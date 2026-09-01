from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.plugins.contracts import PluginContractError, PluginLifecycleState
from packages.plugins.runtime_profiles import default_runtime_profiles
from packages.plugins.service import PluginPlatformError, plugin_platform

router = APIRouter(prefix="/api/plugin-platform", tags=["plugin-platform"])


class PublishPluginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest: dict[str, Any]
    package_artifact_id: str | None = Field(default=None, max_length=80)
    sbom_artifact_id: str | None = Field(default=None, max_length=80)
    source_digest: str | None = Field(default=None, max_length=64)


class InstallPluginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: str = Field(min_length=1, max_length=80)
    granted_permissions: list[str] = Field(default_factory=list)
    configuration: dict[str, Any] = Field(default_factory=dict)


class UpdateInstallationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: PluginLifecycleState
    enabled: bool | None = None
    configuration: dict[str, Any] | None = None


def _owner(auth: AuthContext) -> None:
    if auth.role != "owner":
        raise HTTPException(status_code=403, detail="Only Workspace owners can manage plugin infrastructure")


@router.get("/foundation")
async def plugin_foundation(auth: AuthContext = Depends(get_auth_context)):
    del auth
    return {
        "schema": "operly.plugin/v1",
        "ai_runtime_enabled": False,
        "kernel_authority": "operly-kernel-v3",
        "principles": {
            "generated_code_in_control_plane": False,
            "provider_credentials_in_plugins": False,
            "plugin_grants_can_expand_workspace_authority": False,
            "exact_artifact_promotion_required": True,
        },
        "digital_workload_classes": ["platform_native", "remote_http", "sandbox_job", "web_service", "worker", "static_site"],
        "platform_primitives": [
            "capabilities",
            "workspace_permissions",
            "approvals",
            "artifacts",
            "service_bindings",
            "runtime_identity",
            "isolated_compute",
            "events",
            "webhooks",
            "storage_namespaces",
            "resource_budgets",
            "workflow",
            "mcp",
        ],
    }


@router.get("/runtime-profiles")
async def runtime_profiles(auth: AuthContext = Depends(get_auth_context)):
    del auth
    return {"profiles": [profile.public_dict() for profile in default_runtime_profiles().all()]}


@router.get("/installations")
async def installations(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return {"installations": await plugin_platform.list_installations(db, tenant_id=auth.tenant.id)}


@router.post("/packages", status_code=201)
async def publish_plugin(
    payload: PublishPluginInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _owner(auth)
    try:
        package, version, manifest = await plugin_platform.publish_workspace_version(
            db,
            tenant_id=auth.tenant.id,
            user_id=auth.user.id,
            manifest_payload=payload.manifest,
            package_artifact_id=payload.package_artifact_id,
            sbom_artifact_id=payload.sbom_artifact_id,
            source_digest=payload.source_digest,
        )
        await db.commit()
    except (PluginContractError, PluginPlatformError, ValueError) as error:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "package_id": package.id,
        "version_id": version.id,
        "plugin_id": package.plugin_id,
        "version": version.version,
        "manifest_digest": version.manifest_digest,
        "validation_status": version.validation_status,
        "capability_count": len(manifest.capabilities),
        "next": "Validate package/supply chain, then install. Installation never imports plugin code into the API process.",
    }


@router.post("/installations", status_code=201)
async def install_plugin(
    payload: InstallPluginInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _owner(auth)
    try:
        row = await plugin_platform.install_version(
            db,
            tenant_id=auth.tenant.id,
            user_id=auth.user.id,
            version_id=payload.version_id,
            granted_permissions=payload.granted_permissions,
            configuration=payload.configuration,
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except (PluginContractError, PluginPlatformError, ValueError) as error:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "installation_id": row.id,
        "status": row.status,
        "enabled": row.enabled,
        "next": "Build/validate/provision through an isolated runtime before transitioning to active.",
    }


@router.patch("/installations/{installation_id}")
async def update_installation(
    installation_id: str,
    payload: UpdateInstallationInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _owner(auth)
    try:
        row = await plugin_platform.set_installation_state(
            db,
            tenant_id=auth.tenant.id,
            installation_id=installation_id,
            status=payload.status,
            enabled=payload.enabled,
            configuration=payload.configuration,
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (PluginContractError, PluginPlatformError, ValueError) as error:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"installation_id": row.id, "status": row.status, "enabled": row.enabled}
