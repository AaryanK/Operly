from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.database.studio_source_models import StudioAgentRun
from packages.studio.service import StudioService


router = APIRouter(tags=["studio-source"])
service = StudioService()


@router.get("/api/studio/projects/{project_id}/source/runs")
async def list_source_runs(
    project_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10_000),
    state: str | None = Query(default=None, max_length=30),
    operation: str | None = Query(default=None, max_length=30),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Newest-first tenant/project-scoped durable Studio run collection."""
    try:
        await service.project(db, auth.tenant.id, project_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Project not found") from error

    query = select(StudioAgentRun).where(
        StudioAgentRun.tenant_id == auth.tenant.id,
        StudioAgentRun.project_id == project_id,
    )
    if state:
        query = query.where(StudioAgentRun.state == state)
    if operation:
        query = query.where(StudioAgentRun.operation == operation)
    rows = (
        await db.scalars(
            query.order_by(StudioAgentRun.created_at.desc(), StudioAgentRun.id.desc())
            .offset(offset)
            .limit(limit + 1)
        )
    ).all()
    has_more = len(rows) > limit
    items = rows[:limit]
    return {
        "items": [
            {
                "id": row.id,
                "projectId": row.project_id,
                "operation": row.operation,
                "instruction": row.instruction[:500],
                "state": row.state,
                "modelId": row.model_id,
                "sourceId": row.source_id,
                "error": row.error_message[:500] if row.error_message else None,
                "eventCount": row.event_count,
                "createdAt": row.created_at.isoformat(),
                "startedAt": row.started_at.isoformat() if row.started_at else None,
                "completedAt": row.completed_at.isoformat() if row.completed_at else None,
            }
            for row in items
        ],
        "limit": limit,
        "offset": offset,
        "nextOffset": offset + limit if has_more else None,
        "hasMore": has_more,
    }
