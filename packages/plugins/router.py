from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.connectors.secrets import store_secret
from packages.database.capability_binding_models import CapabilityBindingRecord
from packages.database.plugin_credential_models import (
    PluginCredentialBindingRecord,
    PluginEgressGrantRecord,
)
from packages.database.plugin_platform_models import (
    DigitalResourceBudgetRecord,
    PluginInstallationRecord,
)
from packages.plugins.budgets import resource_budgets
from packages.plugins.capability_bindings import capability_bindings
from packages.plugins.contracts import PluginContractError, PluginLifecycleState
from packages.plugins.credentials import plugin_credentials
from packages.plugins.runtime_profiles import default_runtime_profiles
from packages.plugins.service import PluginPlatformError, plugin_platform
from packages.security.execution_context import ExecutionContext, resolve_execution_context
from packages.security.surfaces import SurfaceKind
from packages.workspace_modules.tools.runtime import build_workspace_runtime

router = APIRouter(prefix="/api/plugin-platform", tags=["plugin-platform"])


class PublishPluginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest: dict[str, Any]
    package_artifact_id: str | None = Field(default=None, max_length=80)
    sbom_artifact_id: str | None = Field(default=None, max_length=80)
    source_digest: str | None = Field(default=None, max_length=64, pattern="^[0-9a-fA-F]{64}$")


class InstallPluginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: str = Field(min_length=1, max_length=80)
    granted_permissions: list[str] = Field(default_factory=list, max_length=200)
    configuration: dict[str, Any] = Field(default_factory=dict)


class UpdateInstallationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: PluginLifecycleState
    enabled: bool | None = None
    configuration: dict[str, Any] | None = None


class BindCredentialInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credential_name: str = Field(min_length=2, max_length=120, pattern="^[a-z][a-z0-9_.-]+$")
    secret_payload: dict[str, str]
    granted_scopes: list[str] = Field(default_factory=list, max_length=100)
    allowed_hosts: list[str] = Field(default_factory=list, max_length=100)


class EgressGrantInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = Field(min_length=1, max_length=253)
    credential_binding_id: str | None = Field(default=None, max_length=80)
    methods: list[str] = Field(default_factory=lambda: ["GET"], max_length=10)
    path_prefixes: list[str] = Field(default_factory=lambda: ["/"], max_length=100)


class CapabilityBindingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    semantic_name: str = Field(min_length=2, max_length=160, pattern="^[a-z][a-z0-9_.-]+$")
    capability_id: str = Field(min_length=2, max_length=200)
    configuration: dict[str, Any] = Field(default_factory=dict)
    argument_constraints: dict[str, Any] = Field(default_factory=dict)
    rate_policy: dict[str, Any] = Field(default_factory=dict)


class ResourceBudgetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: str = Field(min_length=2, max_length=80, pattern="^[a-z][a-z0-9_.-]+$")
    window_seconds: int = Field(ge=60, le=31 * 24 * 60 * 60)
    hard_limit: int = Field(ge=0, le=2_147_483_647)
    soft_limit: int | None = Field(default=None, ge=0, le=2_147_483_647)


def _owner(auth: AuthContext) -> None:
    if auth.role != "owner":
        raise HTTPException(status_code=403, detail="Only Workspace owners can manage plugin infrastructure")


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


async def _workspace_context(db: AsyncSession, auth: AuthContext) -> ExecutionContext:
    try:
        return await resolve_execution_context(
            db,
            workspace_id=auth.tenant.id,
            user_id=auth.user.id,
            channel="web",
            surface=SurfaceKind.WORKSPACE_PRIVATE,
            metadata={"ingress": "plugin_platform_management"},
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


def _safe_json(value: str | None, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed
    except (TypeError, json.JSONDecodeError):
        return fallback


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
            "runtime_identity_is_short_lived": True,
            "workspace_authority_is_re_resolved_per_call": True,
        },
        "digital_workload_classes": [
            "platform_native",
            "remote_http",
            "sandbox_job",
            "web_service",
            "worker",
            "static_site",
        ],
        "platform_primitives": [
            "capabilities",
            "workspace_permissions",
            "approvals",
            "artifacts",
            "capability_bindings",
            "credential_handles",
            "egress_grants",
            "runtime_identity",
            "isolated_compute",
            "events",
            "webhooks",
            "storage_namespaces",
            "resource_budgets",
            "usage_ledger",
            "platform_jobs",
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
        "package_id": package.id,
        "version_id": version.id,
        "plugin_id": package.plugin_id,
        "version": version.version,
        "manifest_digest": version.manifest_digest,
        "validation_status": version.validation_status,
        "capability_count": len(manifest.capabilities),
        "validation_job_queued": True,
        "next": "Operly Worker validates the immutable package. Installation never imports plugin code into the API process.",
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
        "next": "Configure credentials/bindings/budgets and provision a validated isolated runtime before activation.",
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
    except PermissionError as error:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except (PluginContractError, PluginPlatformError, ValueError) as error:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"installation_id": row.id, "status": row.status, "enabled": row.enabled}


@router.get("/installations/{installation_id}/credentials")
async def list_credentials(
    installation_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _owner(auth)
    await _installation(db, tenant_id=auth.tenant.id, installation_id=installation_id)
    rows = list(
        (
            await db.scalars(
                select(PluginCredentialBindingRecord)
                .where(
                    PluginCredentialBindingRecord.tenant_id == auth.tenant.id,
                    PluginCredentialBindingRecord.installation_id == installation_id,
                )
                .order_by(PluginCredentialBindingRecord.created_at.asc())
            )
        ).all()
    )
    grants = list(
        (
            await db.scalars(
                select(PluginEgressGrantRecord).where(
                    PluginEgressGrantRecord.tenant_id == auth.tenant.id,
                    PluginEgressGrantRecord.installation_id == installation_id,
                )
            )
        ).all()
    )
    grants_by_binding: dict[str, list[dict[str, Any]]] = {}
    for grant in grants:
        if not grant.credential_binding_id:
            continue
        grants_by_binding.setdefault(grant.credential_binding_id, []).append(
            {
                "id": grant.id,
                "host": grant.host,
                "methods": _safe_json(grant.methods_json, []),
                "path_prefixes": _safe_json(grant.path_prefixes_json, []),
                "enabled": grant.enabled,
            }
        )
    return {
        "credentials": [
            {
                "id": row.id,
                "credential_name": row.credential_name,
                "credential_type": row.credential_type,
                "granted_scopes": _safe_json(row.granted_scopes_json, []),
                "allowed_hosts": _safe_json(row.allowed_hosts_json, []),
                "status": row.status,
                "egress_grants": grants_by_binding.get(row.id, []),
                "secret_exposed": False,
            }
            for row in rows
        ]
    }


@router.post("/installations/{installation_id}/credentials", status_code=201)
async def bind_credential(
    installation_id: str,
    payload: BindCredentialInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _owner(auth)
    await _installation(db, tenant_id=auth.tenant.id, installation_id=installation_id)
    serialized_secret = json.dumps(payload.secret_payload, separators=(",", ":"), ensure_ascii=False)
    if not payload.secret_payload or len(serialized_secret.encode("utf-8")) > 16 * 1024:
        raise HTTPException(status_code=422, detail="Credential payload must be non-empty and at most 16 KiB")
    try:
        secret_reference = await store_secret(
            db,
            auth.tenant.id,
            {
                "purpose": "plugin_credential",
                "credential_name": payload.credential_name,
                "value": payload.secret_payload,
            },
        )
        handle = await plugin_credentials.bind_secret(
            db,
            tenant_id=auth.tenant.id,
            installation_id=installation_id,
            credential_name=payload.credential_name,
            secret_reference=secret_reference,
            granted_scopes=payload.granted_scopes,
            allowed_hosts=payload.allowed_hosts,
            created_by=auth.user.id,
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        await db.rollback()
        raise HTTPException(status_code=503, detail="Encrypted Workspace credential storage is unavailable") from error
    return {
        "credential_binding_id": handle.binding_id,
        "credential_name": handle.credential_name,
        "credential_type": handle.credential_type,
        "granted_scopes": list(handle.granted_scopes),
        "allowed_hosts": list(handle.allowed_hosts),
        "secret_exposed": False,
    }


@router.delete("/installations/{installation_id}/credentials/{credential_name}")
async def revoke_credential(
    installation_id: str,
    credential_name: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _owner(auth)
    await _installation(db, tenant_id=auth.tenant.id, installation_id=installation_id)
    try:
        await plugin_credentials.revoke_binding(
            db,
            tenant_id=auth.tenant.id,
            installation_id=installation_id,
            credential_name=credential_name,
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"revoked": True, "credential_name": credential_name}


@router.post("/installations/{installation_id}/egress-grants", status_code=201)
async def create_egress_grant(
    installation_id: str,
    payload: EgressGrantInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _owner(auth)
    await _installation(db, tenant_id=auth.tenant.id, installation_id=installation_id)
    try:
        row = await plugin_credentials.create_egress_grant(
            db,
            tenant_id=auth.tenant.id,
            installation_id=installation_id,
            host=payload.host,
            credential_binding_id=payload.credential_binding_id,
            methods=payload.methods,
            path_prefixes=payload.path_prefixes,
            created_by=auth.user.id,
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "id": row.id,
        "host": row.host,
        "credential_binding_id": row.credential_binding_id,
        "methods": _safe_json(row.methods_json, []),
        "path_prefixes": _safe_json(row.path_prefixes_json, []),
        "enabled": row.enabled,
    }


@router.delete("/installations/{installation_id}/egress-grants/{grant_id}")
async def revoke_egress_grant(
    installation_id: str,
    grant_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _owner(auth)
    await _installation(db, tenant_id=auth.tenant.id, installation_id=installation_id)
    row = await db.scalar(
        select(PluginEgressGrantRecord).where(
            PluginEgressGrantRecord.id == grant_id,
            PluginEgressGrantRecord.tenant_id == auth.tenant.id,
            PluginEgressGrantRecord.installation_id == installation_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Egress grant not found")
    row.enabled = False
    await db.commit()
    return {"revoked": True, "id": row.id}


@router.get("/installations/{installation_id}/bindings")
async def list_capability_bindings(
    installation_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _owner(auth)
    await _installation(db, tenant_id=auth.tenant.id, installation_id=installation_id)
    rows = await capability_bindings.list_for_subject(
        db,
        tenant_id=auth.tenant.id,
        subject_kind="plugin_installation",
        subject_id=installation_id,
    )
    return {
        "bindings": [
            {
                "id": row.id,
                "semantic_name": row.semantic_name,
                "capability_id": row.capability_id,
                "capability_version": row.capability_version,
                "authority_user_id": row.authority_user_id,
                "argument_constraints": _safe_json(row.argument_constraints_json, {}),
                "rate_policy": _safe_json(row.rate_policy_json, {}),
                "status": row.status,
                "enabled": row.enabled,
            }
            for row in rows
        ]
    }


@router.post("/installations/{installation_id}/bindings", status_code=201)
async def create_capability_binding(
    installation_id: str,
    payload: CapabilityBindingInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _owner(auth)
    await _installation(db, tenant_id=auth.tenant.id, installation_id=installation_id)
    context = await _workspace_context(db, auth)
    runtime = build_workspace_runtime()
    available = await runtime.available_capabilities(
        db,
        context=context,
        query=payload.capability_id,
        limit=1000,
    )
    spec = next((item for item in available if item.id == payload.capability_id), None)
    if spec is None:
        raise HTTPException(
            status_code=403,
            detail="Capability is not currently authorized or available to this Workspace principal",
        )
    try:
        row = await capability_bindings.create(
            db,
            context=context,
            subject_kind="plugin_installation",
            subject_id=installation_id,
            semantic_name=payload.semantic_name,
            capability=spec,
            configuration=payload.configuration,
            argument_constraints=payload.argument_constraints,
            rate_policy=payload.rate_policy,
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "id": row.id,
        "semantic_name": row.semantic_name,
        "capability_id": row.capability_id,
        "capability_version": row.capability_version,
        "authority_user_id": row.authority_user_id,
        "enabled": row.enabled,
        "provider_credentials_copied": False,
    }


@router.delete("/installations/{installation_id}/bindings/{binding_id}")
async def revoke_capability_binding(
    installation_id: str,
    binding_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _owner(auth)
    await _installation(db, tenant_id=auth.tenant.id, installation_id=installation_id)
    row = await db.scalar(
        select(CapabilityBindingRecord).where(
            CapabilityBindingRecord.id == binding_id,
            CapabilityBindingRecord.tenant_id == auth.tenant.id,
            CapabilityBindingRecord.subject_kind == "plugin_installation",
            CapabilityBindingRecord.subject_id == installation_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Capability binding not found")
    await capability_bindings.revoke(
        db,
        tenant_id=auth.tenant.id,
        binding_id=binding_id,
    )
    await db.commit()
    return {"revoked": True, "id": binding_id}


@router.get("/installations/{installation_id}/budgets")
async def list_resource_budgets(
    installation_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _owner(auth)
    await _installation(db, tenant_id=auth.tenant.id, installation_id=installation_id)
    rows = list(
        (
            await db.scalars(
                select(DigitalResourceBudgetRecord)
                .where(
                    DigitalResourceBudgetRecord.tenant_id == auth.tenant.id,
                    DigitalResourceBudgetRecord.subject_kind == "plugin_installation",
                    DigitalResourceBudgetRecord.subject_id == installation_id,
                )
                .order_by(
                    DigitalResourceBudgetRecord.metric.asc(),
                    DigitalResourceBudgetRecord.window_seconds.asc(),
                )
            )
        ).all()
    )
    return {
        "budgets": [
            {
                "id": row.id,
                "metric": row.metric,
                "window_seconds": row.window_seconds,
                "hard_limit": row.hard_limit,
                "soft_limit": row.soft_limit,
                "enabled": row.enabled,
            }
            for row in rows
        ]
    }


@router.post("/installations/{installation_id}/budgets", status_code=201)
async def configure_resource_budget(
    installation_id: str,
    payload: ResourceBudgetInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _owner(auth)
    await _installation(db, tenant_id=auth.tenant.id, installation_id=installation_id)
    try:
        row = await resource_budgets.configure(
            db,
            tenant_id=auth.tenant.id,
            subject_kind="plugin_installation",
            subject_id=installation_id,
            metric=payload.metric,
            window_seconds=payload.window_seconds,
            hard_limit=payload.hard_limit,
            soft_limit=payload.soft_limit,
        )
        await db.commit()
    except ValueError as error:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "id": row.id,
        "metric": row.metric,
        "window_seconds": row.window_seconds,
        "hard_limit": row.hard_limit,
        "soft_limit": row.soft_limit,
        "enabled": row.enabled,
    }
