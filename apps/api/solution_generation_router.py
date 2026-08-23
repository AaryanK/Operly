from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.solutions.composer import retry_solution_initial_generation
from packages.solutions.service import SolutionService, solution_json


router = APIRouter(prefix="/api/solutions", tags=["solutions"])
service = SolutionService()


@router.post("/{solution_id}/retry-generation")
async def retry_solution_generation(
    solution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Retry managed-app initial generation from the stored owner objective."""
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
