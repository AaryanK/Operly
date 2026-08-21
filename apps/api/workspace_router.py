from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from apps.api.schemas import (
    MemoryCreate,
    TaskCreate,
    WorkspaceCreateInput,
    WorkspaceMemberAddInput,
    WorkspaceMemberRoleInput,
    WorkspaceRoleCreateInput,
    WorkspaceRolePermissionsInput,
)
from apps.api.security import normalize_email
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.workspace_security_models import WorkspaceRole, WorkspaceRolePermission
from packages.security.permissions import (
    DEFAULT_ROLE_AUTHORITY,
    normalize_role_key,
    resolve_workspace_permissions,
    validate_permissions,
)
from packages.workspace.service import WorkspaceService

router = APIRouter(prefix="/api", tags=["workspace"])


async def _require_workspace_permission(
    db: AsyncSession,
    auth: AuthContext,
    permission: str,
) -> set[str]:
    permissions = await resolve_workspace_permissions(
        db,
        tenant_id=auth.tenant.id,
        role=auth.role,
    )
    # Workspace ownership is an application-level authority boundary and cannot
    # be accidentally removed by editing a role permission list.
    if auth.role == "owner" or permission in permissions:
        return permissions
    raise HTTPException(status_code=403, detail="Workspace permission denied")


async def _role_exists(db: AsyncSession, tenant_id: str, role_key: str) -> bool:
    if role_key in DEFAULT_ROLE_AUTHORITY:
        return True
    return bool(
        await db.scalar(
            select(WorkspaceRole.id).where(
                WorkspaceRole.tenant_id == tenant_id,
                WorkspaceRole.key == role_key,
            )
        )
    )


@router.post("/workspaces", status_code=201)
async def create_workspace(
    payload: WorkspaceCreateInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    name = " ".join(payload.name.replace("\x00", "").split()).strip()
    if not name:
        raise HTTPException(status_code=422, detail="Workspace name is required")
    timezone = " ".join(payload.timezone.replace("\x00", "").split()).strip() or "UTC"
    tenant = Tenant(name=name[:200], timezone=timezone[:100], slug=None)
    db.add(tenant)
    await db.flush()
    db.add(TenantMember(tenant_id=tenant.id, user_id=auth.user.id, role="owner"))
    await db.commit()
    return {
        "id": tenant.id,
        "name": tenant.name,
        "timezone": tenant.timezone,
        "role": "owner",
        "current": False,
    }


@router.get("/workspace/roles")
async def list_workspace_roles(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_workspace_permission(db, auth, "workspace:read")
    custom_rows = (
        await db.scalars(
            select(WorkspaceRole)
            .where(WorkspaceRole.tenant_id == auth.tenant.id)
            .order_by(WorkspaceRole.name)
        )
    ).all()
    custom_by_key = {row.key: row for row in custom_rows}
    keys = sorted(set(DEFAULT_ROLE_AUTHORITY) | set(custom_by_key))
    result = []
    for key in keys:
        row = custom_by_key.get(key)
        permissions = await resolve_workspace_permissions(
            db,
            tenant_id=auth.tenant.id,
            role=key,
        )
        result.append(
            {
                "key": key,
                "name": row.name if row else key.replace("-", " ").title(),
                "customized": row is not None,
                "permissions": sorted(permissions),
            }
        )
    return result


@router.post("/workspace/roles", status_code=201)
async def create_workspace_role(
    payload: WorkspaceRoleCreateInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_workspace_permission(db, auth, "workspace:roles:manage")
    try:
        key = normalize_role_key(payload.key or payload.name)
        permissions = validate_permissions(payload.permissions)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if key in DEFAULT_ROLE_AUTHORITY:
        raise HTTPException(
            status_code=409,
            detail="Use the permissions endpoint to customize a built-in role",
        )
    role = WorkspaceRole(
        tenant_id=auth.tenant.id,
        key=key,
        name=" ".join(payload.name.split())[:120],
        is_system=False,
    )
    db.add(role)
    try:
        await db.flush()
        db.add_all(
            WorkspaceRolePermission(role_id=role.id, permission=permission)
            for permission in sorted(permissions)
        )
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Role already exists") from error
    return {
        "key": role.key,
        "name": role.name,
        "permissions": sorted(permissions),
    }


@router.put("/workspace/roles/{role_key}/permissions")
async def set_workspace_role_permissions(
    role_key: str,
    payload: WorkspaceRolePermissionsInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_workspace_permission(db, auth, "workspace:roles:manage")
    try:
        key = normalize_role_key(role_key)
        permissions = validate_permissions(payload.permissions)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    role = await db.scalar(
        select(WorkspaceRole).where(
            WorkspaceRole.tenant_id == auth.tenant.id,
            WorkspaceRole.key == key,
        )
    )
    if role is None:
        if key not in DEFAULT_ROLE_AUTHORITY:
            raise HTTPException(status_code=404, detail="Workspace role not found")
        role = WorkspaceRole(
            tenant_id=auth.tenant.id,
            key=key,
            name=key.replace("-", " ").title(),
            is_system=True,
        )
        db.add(role)
        await db.flush()

    await db.execute(
        delete(WorkspaceRolePermission).where(WorkspaceRolePermission.role_id == role.id)
    )
    db.add_all(
        WorkspaceRolePermission(role_id=role.id, permission=permission)
        for permission in sorted(permissions)
    )
    await db.commit()
    return {
        "key": role.key,
        "name": role.name,
        "permissions": sorted(permissions),
    }


@router.get("/workspace/members")
async def list_workspace_members(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_workspace_permission(db, auth, "workspace:read")
    rows = (
        await db.execute(
            select(TenantMember, AppUser)
            .join(AppUser, AppUser.id == TenantMember.user_id)
            .where(TenantMember.tenant_id == auth.tenant.id)
            .order_by(AppUser.display_name, AppUser.email)
        )
    ).all()
    return [
        {
            "user_id": user.id,
            "display_name": user.display_name,
            "email": user.email,
            "role": membership.role,
        }
        for membership, user in rows
    ]


@router.post("/workspace/members", status_code=201)
async def add_workspace_member(
    payload: WorkspaceMemberAddInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_workspace_permission(db, auth, "workspace:members:manage")
    try:
        role_key = normalize_role_key(payload.role)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not await _role_exists(db, auth.tenant.id, role_key):
        raise HTTPException(status_code=422, detail="Workspace role not found")
    try:
        email = normalize_email(payload.email)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    user = await db.scalar(select(AppUser).where(AppUser.email == email))
    if user is None or not user.active:
        raise HTTPException(
            status_code=404,
            detail="Operly user not found. Invite-by-email onboarding is not enabled yet.",
        )
    membership = TenantMember(
        tenant_id=auth.tenant.id,
        user_id=user.id,
        role=role_key,
    )
    db.add(membership)
    try:
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail="User is already a workspace member") from error
    return {
        "user_id": user.id,
        "display_name": user.display_name,
        "email": user.email,
        "role": role_key,
    }


@router.patch("/workspace/members/{user_id}/role")
async def set_workspace_member_role(
    user_id: str,
    payload: WorkspaceMemberRoleInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_workspace_permission(db, auth, "workspace:members:manage")
    try:
        role_key = normalize_role_key(payload.role)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not await _role_exists(db, auth.tenant.id, role_key):
        raise HTTPException(status_code=422, detail="Workspace role not found")
    membership = await db.scalar(
        select(TenantMember).where(
            TenantMember.tenant_id == auth.tenant.id,
            TenantMember.user_id == user_id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=404, detail="Workspace member not found")
    if membership.role == "owner" and role_key != "owner":
        owners = await db.scalar(
            select(func.count(TenantMember.id)).where(
                TenantMember.tenant_id == auth.tenant.id,
                TenantMember.role == "owner",
            )
        )
        if int(owners or 0) <= 1:
            raise HTTPException(status_code=409, detail="A workspace must keep at least one owner")
    membership.role = role_key
    await db.commit()
    return {"user_id": membership.user_id, "role": membership.role}


@router.get("/dashboard")
async def dashboard(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_workspace_permission(db, auth, "workspace:read")
    return await WorkspaceService.dashboard(db, auth.tenant.id)


@router.get("/messages")
async def list_messages(
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=250),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_workspace_permission(db, auth, "messages:read")
    rows = await WorkspaceService.list_messages(
        db,
        auth.tenant.id,
        search=search,
        limit=limit,
    )
    return [
        {
            "id": row.id,
            "message_id": str(row.message_id),
            "channel_id": str(row.channel_id),
            "author_name": row.author_name,
            "content": row.content,
            "is_bot": row.is_bot,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.get("/tasks")
async def list_tasks(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_workspace_permission(db, auth, "tasks:read")
    rows = await WorkspaceService.list_tasks(db, auth.tenant.id)
    return [
        {
            "id": row.id,
            "title": row.title,
            "status": row.status,
            "due_at": row.due_at.isoformat() if row.due_at else None,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.post("/tasks")
async def create_task(
    payload: TaskCreate,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_workspace_permission(db, auth, "tasks:write")
    try:
        row = await WorkspaceService.create_task(
            db,
            auth.tenant.id,
            title=payload.title,
            due_at=payload.due_at,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    await db.commit()
    return {
        "id": row.id,
        "title": row.title,
        "status": row.status,
        "due_at": row.due_at.isoformat() if row.due_at else None,
        "created_at": row.created_at.isoformat(),
    }


@router.patch("/tasks/{task_id}/complete")
async def complete_task(
    task_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_workspace_permission(db, auth, "tasks:write")
    try:
        await WorkspaceService.complete_task(db, auth.tenant.id, task_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    await db.commit()
    return {"ok": True}


@router.get("/memories")
async def list_memories(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_workspace_permission(db, auth, "memory:read")
    rows = await WorkspaceService.list_memories(db, auth.tenant.id)
    return [
        {
            "id": row.id,
            "kind": row.kind,
            "content": row.content,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.post("/memories")
async def create_memory(
    payload: MemoryCreate,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_workspace_permission(db, auth, "memory:write")
    try:
        row = await WorkspaceService.create_memory(
            db,
            auth.tenant.id,
            kind=payload.kind,
            content=payload.content,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    await db.commit()
    return {
        "id": row.id,
        "kind": row.kind,
        "content": row.content,
        "created_at": row.created_at.isoformat(),
    }
