"""Owner-only debugging endpoints for Studio model traffic."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.database.studio_source_models import StudioAgentRun
from packages.studio.model_trace import trace_json, trace_rows

router = APIRouter(tags=["studio-debug"])


def _assert_owner(auth: AuthContext) -> None:
    if auth.role != "owner":
        raise HTTPException(status_code=403, detail="Only owners can inspect Studio model traces")


@router.get("/api/studio/projects/{project_id}/source/runs/{run_id}/model-trace")
async def get_studio_model_trace(
    project_id: str,
    run_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Return the redacted exact model request/response trace for one Studio run.

    The normal Activity API intentionally stays compact. This endpoint may contain
    business context, source text, conversation messages, and tool schemas, so it is
    owner-only and never returns provider credentials. ``exactPayloadDigest`` is the
    SHA-256 of the exact unredacted JSON observed at the model boundary.
    """
    _assert_owner(auth)
    run = await db.get(StudioAgentRun, run_id)
    if run is None or run.tenant_id != auth.tenant.id or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Studio agent run not found")

    rows = await trace_rows(db, auth.tenant.id, run.id)
    return {
        "runId": run.id,
        "projectId": run.project_id,
        "operation": run.operation,
        "runState": run.state,
        "traceVersion": 1,
        "redactionApplied": True,
        "entryCount": len(rows),
        "entries": [trace_json(row) for row in rows],
    }
