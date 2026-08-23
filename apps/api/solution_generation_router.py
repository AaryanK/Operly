import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.solutions.composer import retry_solution_initial_generation
from packages.solutions.service import SolutionService, solution_json


router = APIRouter(prefix="/api/solutions", tags=["solutions"])
service = SolutionService()


@router.get("/{solution_id}/architecture")
async def solution_architecture(
    solution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Return the runtime-neutral capability graph for a composed Solution."""
    try:
        row = await service.get(db, auth.tenant.id, solution_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    try:
        context = json.loads(row.context_json or "{}")
    except Exception:
        context = {}
    manifest = context.get("solutionManifest") if isinstance(context, dict) else None
    if not isinstance(manifest, dict):
        raise HTTPException(
            status_code=404,
            detail="This legacy Solution does not have a capability manifest yet",
        )
    return manifest


@router.post("/{solution_id}/retry-generation")
async def retry_solution_generation(
    solution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Retry initial generation from the stored owner objective and capability graph."""
    if auth.role != "owner":
        raise HTTPException(status_code=403, detail="Only owners can retry Solution generation")
    try:
        row = await retry_solution_initial_generation(
            db,
            tenant_id=auth.tenant.id,
            user_id=auth.user.id,
            solution_id=solution_id,
            service=service,
        )
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    await db.commit()
    return {"solution": solution_json(row)}
