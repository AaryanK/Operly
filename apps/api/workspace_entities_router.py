"""Runtime gateway for canonical workspace entities used by generated Solutions."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from packages.relational_data.tokens import BindingGrantError, verify_capability_grant
from packages.workspace_entities.contracts import (
    WORKSPACE_ENTITY_CAPABILITY_ID,
    CANONICAL_ENTITY_SCHEMAS,
    EntityCreate,
    EntityList,
    EntityUpdate,
)
from packages.workspace_entities.store import WorkspaceEntityError, WorkspaceEntityStore

router = APIRouter(prefix="/runtime/entities", tags=["runtime-workspace-entities"])
_store: WorkspaceEntityStore | None = None


def _bearer(request: Request) -> str:
    value = request.headers.get("Authorization", "")
    if not value.startswith("Bearer ") or len(value) <= 7:
        raise HTTPException(401, "runtime_binding_authorization_required")
    return value[7:]


def _claims(request: Request, scope: str, kind: str | None = None):
    try:
        return verify_capability_grant(
            _bearer(request),
            capability_id=WORKSPACE_ENTITY_CAPABILITY_ID,
            required_scope=scope,
            allowed_scopes=frozenset({"read", "write"}),
            required_resource=kind,
        )
    except BindingGrantError as error:
        raise HTTPException(401, "runtime_binding_authorization_invalid") from error


def _entity_store() -> WorkspaceEntityStore:
    global _store
    if _store is None:
        try:
            _store = WorkspaceEntityStore()
        except Exception as error:
            raise HTTPException(503, "workspace_entity_plane_unavailable") from error
    return _store


def set_workspace_entity_store_for_testing(store: WorkspaceEntityStore | None) -> None:
    global _store
    _store = store


@router.get("/schema")
async def entity_schema(request: Request):
    claims = _claims(request, "read")
    return {
        "schemaVersion": "operly.workspace-entities/v1",
        "workspaceId": claims.workspace_id,
        "applicationId": claims.application_id,
        "authorizedKinds": list(claims.resources),
        "entities": {kind: CANONICAL_ENTITY_SCHEMAS[kind] for kind in claims.resources},
    }


@router.post("/list")
async def list_entities(request: Request, payload: EntityList):
    claims = _claims(request, "read", payload.kind)
    try:
        return await _entity_store().list(claims.workspace_id, payload)
    except WorkspaceEntityError as error:
        raise HTTPException(400, str(error)[:1000]) from error


@router.get("/{kind}/{entity_id}")
async def get_entity(kind: str, entity_id: str, request: Request):
    claims = _claims(request, "read", kind)
    try:
        return await _entity_store().get(claims.workspace_id, kind, entity_id)
    except WorkspaceEntityError as error:
        raise HTTPException(404, str(error)[:1000]) from error


@router.post("/create")
async def create_entity(request: Request, payload: EntityCreate):
    claims = _claims(request, "write", payload.kind)
    try:
        return await _entity_store().create(claims.workspace_id, payload)
    except WorkspaceEntityError as error:
        raise HTTPException(400, str(error)[:1000]) from error


@router.post("/update")
async def update_entity(request: Request, payload: EntityUpdate):
    claims = _claims(request, "write", payload.kind)
    try:
        return await _entity_store().update(claims.workspace_id, payload)
    except WorkspaceEntityError as error:
        raise HTTPException(400, str(error)[:1000]) from error


__all__ = ["router", "set_workspace_entity_store_for_testing"]
