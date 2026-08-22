from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.capabilities.agent_harness import PluginAgentHarness, PluginInvocationContext
from packages.service_bindings import ServiceBindingResolver, ServiceBindingStore
from packages.software_projects import SoftwareProjectService


router = APIRouter(prefix="/api/software-projects", tags=["software-projects"])
projects = SoftwareProjectService()


class ProjectCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=8000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BindingCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    semantic_name: str = Field(min_length=1, max_length=160)
    capability_id: str = Field(min_length=1, max_length=200)
    binding_mode: str = Field(default="capability_gateway", max_length=40)
    principal_scope: str = Field(default="project_runtime", max_length=80)
    configuration: dict[str, Any] = Field(default_factory=dict)


def _owner(auth: AuthContext) -> None:
    if auth.role != "owner":
        raise HTTPException(status_code=403, detail="Only owners can change software projects")


def _project_json(project) -> dict[str, Any]:
    return {
        "id": project.id,
        "workspaceId": project.workspace_id,
        "name": project.name,
        "description": project.description,
        "state": project.state.value,
        "activeSourceVersionId": project.active_source_version_id,
        "activeRuntimeId": project.active_runtime_id,
        "serviceBindingIds": list(project.service_binding_ids),
        "metadata": project.metadata,
        "createdBy": project.created_by,
        "createdAt": project.created_at.isoformat() if project.created_at else None,
        "updatedAt": project.updated_at.isoformat() if project.updated_at else None,
    }


def _binding_json(binding) -> dict[str, Any]:
    return {
        "id": binding.id,
        "projectId": binding.project_id,
        "workspaceId": binding.workspace_id,
        "semanticName": binding.semantic_name,
        "capabilityId": binding.capability_id,
        "capabilityVersion": binding.capability_version,
        "bindingMode": binding.binding_mode,
        "principalScope": binding.principal_scope,
        "configuration": dict(binding.configuration),
        "createdAt": binding.created_at.isoformat() if binding.created_at else None,
    }


async def _registry(auth: AuthContext):
    harness = PluginAgentHarness()
    context = PluginInvocationContext(
        tenant_id=auth.tenant.id,
        user_id=auth.user.id,
        role=auth.role,
        objective="Configure software project service bindings",
        channel="web",
        metadata={"role": auth.role, "allow_tenant_context": True},
    )
    return await harness.registry_for(context), await harness.authority_for(context)


@router.get("")
async def list_projects(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = await projects.list(db, auth.tenant.id)
    # Legacy synchronization can materialize canonical identities on first read.
    await db.commit()
    return [_project_json(row) for row in rows]


@router.post("", status_code=201)
async def create_project(
    payload: ProjectCreateInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _owner(auth)
    row = await projects.create(
        db,
        workspace_id=auth.tenant.id,
        user_id=auth.user.id,
        name=payload.name,
        description=payload.description,
        metadata=payload.metadata,
    )
    await db.commit()
    return _project_json(row)


@router.get("/{project_id}")
async def get_project(
    project_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        row = await projects.get(db, auth.tenant.id, project_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    await db.commit()
    return _project_json(row)


@router.get("/{project_id}/binding-candidates")
async def binding_candidates(
    project_id: str,
    operation: str = Query(min_length=1, max_length=1000),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        await projects.get(db, auth.tenant.id, project_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    registry, authority = await _registry(auth)
    rows = ServiceBindingResolver(registry).candidates(
        workspace_id=auth.tenant.id,
        operation=operation,
        authority=authority,
    )
    await db.commit()
    return [
        {
            "capabilityId": row.capability_id,
            "version": row.version,
            "displayName": row.display_name,
            "description": row.description,
            "risk": row.risk,
            "authorized": row.authorized,
            "configured": row.configured,
            "score": row.score,
        }
        for row in rows
    ]


@router.get("/{project_id}/bindings")
async def list_bindings(
    project_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    registry, _ = await _registry(auth)
    store = ServiceBindingStore(registry)
    try:
        rows = await store.list(db, workspace_id=auth.tenant.id, project_id=project_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return [_binding_json(row) for row in rows]


@router.post("/{project_id}/bindings", status_code=201)
async def create_binding(
    project_id: str,
    payload: BindingCreateInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _owner(auth)
    registry, _ = await _registry(auth)
    store = ServiceBindingStore(registry)
    try:
        row = await store.create(
            db,
            workspace_id=auth.tenant.id,
            project_id=project_id,
            user_id=auth.user.id,
            semantic_name=payload.semantic_name,
            capability_id=payload.capability_id,
            binding_mode=payload.binding_mode,
            principal_scope=payload.principal_scope,
            configuration=payload.configuration,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, PermissionError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    await db.commit()
    return _binding_json(row)


@router.delete("/{project_id}/bindings/{binding_id}")
async def revoke_binding(
    project_id: str,
    binding_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _owner(auth)
    registry, _ = await _registry(auth)
    store = ServiceBindingStore(registry)
    try:
        binding = await store.get(db, workspace_id=auth.tenant.id, binding_id=binding_id)
        if binding.project_id != project_id:
            raise LookupError("Service binding not found")
        await store.revoke(db, workspace_id=auth.tenant.id, binding_id=binding_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    await db.commit()
    return {"ok": True, "bindingId": binding_id, "status": "revoked"}
