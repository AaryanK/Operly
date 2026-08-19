from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from apps.api.schemas import MemoryCreate, TaskCreate
from packages.workspace.service import WorkspaceService

router = APIRouter(prefix="/api", tags=["workspace"])


@router.get("/dashboard")
async def dashboard(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await WorkspaceService.dashboard(db, auth.tenant.id)


@router.get("/messages")
async def list_messages(
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=250),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
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
