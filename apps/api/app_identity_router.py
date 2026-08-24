"""Runtime and owner-control APIs for generated-application identities."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.app_identity.contracts import (
    APP_IDENTITY_CAPABILITY_ID,
    AcceptInvitationRequest,
    InvitationCreateRequest,
    LoginRequest,
    RegisterRequest,
    SessionRequest,
)
from packages.app_identity.store import AppIdentityError, AppIdentityStore
from packages.database.custom_software_models import GeneratedSourceBundle
from packages.relational_data.tokens import BindingGrantError, verify_capability_grant
from packages.workspace_entities.store import WorkspaceEntityError, WorkspaceEntityStore

runtime_router = APIRouter(prefix="/api/runtime/app-identity", tags=["runtime-app-identity"])
admin_router = APIRouter(prefix="/api/app-identities", tags=["app-identities"])
_store: AppIdentityStore | None = None
_entities: WorkspaceEntityStore | None = None


def _identity_store() -> AppIdentityStore:
    global _store
    if _store is None:
        try:
            _store = AppIdentityStore()
        except Exception as error:
            raise HTTPException(503, "app_identity_plane_unavailable") from error
    return _store


def _entity_store() -> WorkspaceEntityStore:
    global _entities
    if _entities is None:
        try:
            _entities = WorkspaceEntityStore()
        except Exception as error:
            raise HTTPException(503, "workspace_entity_plane_unavailable") from error
    return _entities


def set_app_identity_store_for_testing(store: AppIdentityStore | None) -> None:
    global _store
    _store = store


def _bearer(request: Request) -> str:
    value = request.headers.get("Authorization", "")
    if not value.startswith("Bearer ") or len(value) <= 7:
        raise HTTPException(401, "runtime_binding_authorization_required")
    return value[7:]


def _claims(request: Request):
    try:
        return verify_capability_grant(
            _bearer(request),
            capability_id=APP_IDENTITY_CAPABILITY_ID,
            required_scope="auth",
            allowed_scopes=frozenset({"auth"}),
        )
    except BindingGrantError as error:
        raise HTTPException(401, "runtime_binding_authorization_invalid") from error


def _identity_error(error: Exception, *, authentication: bool = False) -> HTTPException:
    if authentication:
        return HTTPException(401, str(error)[:500])
    return HTTPException(400, str(error)[:500])


@runtime_router.post("/register")
async def register(request: Request, payload: RegisterRequest):
    claims = _claims(request)
    try:
        return await _identity_store().register(claims.workspace_id, claims.application_id, payload)
    except AppIdentityError as error:
        raise _identity_error(error) from error


@runtime_router.post("/login")
async def login(request: Request, payload: LoginRequest):
    claims = _claims(request)
    try:
        return await _identity_store().login(
            claims.workspace_id,
            claims.application_id,
            payload.email,
            payload.password,
        )
    except AppIdentityError as error:
        raise _identity_error(error, authentication=True) from error


@runtime_router.post("/session")
async def session(request: Request, payload: SessionRequest):
    claims = _claims(request)
    try:
        return await _identity_store().verify_session(
            claims.workspace_id,
            claims.application_id,
            payload.sessionToken,
        )
    except AppIdentityError as error:
        raise _identity_error(error, authentication=True) from error


@runtime_router.post("/logout")
async def logout(request: Request, payload: SessionRequest):
    claims = _claims(request)
    try:
        return await _identity_store().logout(
            claims.workspace_id,
            claims.application_id,
            payload.sessionToken,
        )
    except AppIdentityError as error:
        raise _identity_error(error) from error


@runtime_router.post("/invitations/accept")
async def accept_invitation(request: Request, payload: AcceptInvitationRequest):
    claims = _claims(request)
    try:
        return await _identity_store().accept_invitation(
            claims.workspace_id,
            claims.application_id,
            payload.invitationToken,
            payload.password,
        )
    except AppIdentityError as error:
        raise _identity_error(error) from error


async def _owned_application(db: AsyncSession, auth: AuthContext, application_id: str) -> None:
    source = await db.scalar(
        select(GeneratedSourceBundle.id).where(
            GeneratedSourceBundle.tenant_id == auth.tenant.id,
            GeneratedSourceBundle.application_id == application_id,
        ).limit(1)
    )
    if source is None:
        raise HTTPException(404, "Generated application not found in this workspace")


def _require_owner(auth: AuthContext) -> None:
    if auth.role != "owner":
        raise HTTPException(403, "Only the workspace owner can provision generated-app identities")


@admin_router.get("/{application_id}/users")
async def list_app_users(
    application_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _require_owner(auth)
    await _owned_application(db, auth, application_id)
    try:
        return {
            "applicationId": application_id,
            "users": await _identity_store().list_users(auth.tenant.id, application_id),
        }
    except AppIdentityError as error:
        raise HTTPException(400, str(error)[:500]) from error


@admin_router.post("/{application_id}/invitations")
async def create_invitation(
    application_id: str,
    payload: InvitationCreateRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _require_owner(auth)
    await _owned_application(db, auth, application_id)
    if payload.entityKind and payload.entityId:
        try:
            entity = await _entity_store().get(auth.tenant.id, payload.entityKind, payload.entityId)
        except WorkspaceEntityError as error:
            raise HTTPException(400, "Linked canonical entity does not exist") from error
        entity_email = str(entity.get("email") or "").strip().lower()
        if entity_email and entity_email != payload.email.strip().lower():
            raise HTTPException(400, "Invitation email must match the linked canonical entity email")
    try:
        invitation = await _identity_store().create_invitation(auth.tenant.id, application_id, payload)
    except AppIdentityError as error:
        raise HTTPException(400, str(error)[:500]) from error
    return {
        "applicationId": application_id,
        "invitation": invitation,
        "delivery": "caller_managed",
    }


__all__ = [
    "runtime_router",
    "admin_router",
    "set_app_identity_store_for_testing",
]