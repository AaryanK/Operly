"""Runtime-only relational data gateway for generated Operly software.

Browser sessions cannot authorize these routes. Every request needs a short-lived
capability grant scoped to exactly one workspace and application.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from packages.relational_data.contracts import (
    DeleteRequest,
    InsertRequest,
    QueryRequest,
    RelationalMigration,
    UpdateRequest,
)
from packages.relational_data.store import RelationalDataError, RelationalDataStore
from packages.relational_data.tokens import BindingGrantError, verify_binding_grant

router = APIRouter(prefix="/api/runtime/relational", tags=["runtime-relational"])
_store: RelationalDataStore | None = None


class MigrationBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    migrations: list[RelationalMigration] = Field(default_factory=list, max_length=1000)


def _bearer(request: Request) -> str:
    value = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not value.startswith(prefix) or len(value) <= len(prefix):
        raise HTTPException(status_code=401, detail="runtime_binding_authorization_required")
    return value[len(prefix) :]


def _claims(request: Request, scope: str):
    try:
        return verify_binding_grant(_bearer(request), required_scope=scope)
    except BindingGrantError as error:
        raise HTTPException(status_code=401, detail="runtime_binding_authorization_invalid") from error


def _data_store() -> RelationalDataStore:
    global _store
    if _store is None:
        try:
            _store = RelationalDataStore()
        except RelationalDataError as error:
            raise HTTPException(status_code=503, detail="relational_data_plane_unavailable") from error
    return _store


def set_relational_store_for_testing(store: RelationalDataStore | None) -> None:
    global _store
    _store = store


async def _execute(request: Request, scope: str, operation, body) -> dict[str, Any]:
    claims = _claims(request, scope)
    store = _data_store()
    try:
        return await operation(claims.workspace_id, claims.application_id, body)
    except (RelationalDataError, ValidationError) as error:
        raise HTTPException(status_code=400, detail=str(error)[:1000]) from error


@router.get("/health")
async def relational_health(request: Request):
    claims = _claims(request, "read")
    store = _data_store()
    try:
        await store.initialize()
    except RelationalDataError as error:
        raise HTTPException(status_code=503, detail="relational_data_plane_unavailable") from error
    return {
        "status": "ready",
        "workspaceId": claims.workspace_id,
        "applicationId": claims.application_id,
    }


@router.post("/migrations/apply")
async def apply_migrations(request: Request, batch: MigrationBatch):
    claims = _claims(request, "migrate")
    try:
        return await _data_store().apply_migrations(
            claims.workspace_id,
            claims.application_id,
            batch.migrations,
        )
    except RelationalDataError as error:
        raise HTTPException(status_code=400, detail=str(error)[:1000]) from error


@router.post("/query")
async def query_rows(request: Request, query: QueryRequest):
    return await _execute(request, "read", _data_store().query, query)


@router.post("/insert")
async def insert_row(request: Request, insert: InsertRequest):
    return await _execute(request, "write", _data_store().insert, insert)


@router.post("/update")
async def update_rows(request: Request, update: UpdateRequest):
    return await _execute(request, "write", _data_store().update, update)


@router.post("/delete")
async def delete_rows(request: Request, delete: DeleteRequest):
    return await _execute(request, "write", _data_store().delete, delete)


__all__ = ["router", "set_relational_store_for_testing"]
