from fastapi import APIRouter, Depends

from apps.api.dependencies import AuthContext, get_auth_context
from packages.coding_harness.engine import build_harness_plan
from packages.custom_software.schema import AgenticProjectInput

router = APIRouter(tags=["coding-harness"])


@router.post("/api/coding-harness/plans")
async def create_harness_plan(payload: AgenticProjectInput, auth: AuthContext = Depends(get_auth_context)):
    """Lower intent to reviewable IRs. This endpoint never executes generated code."""
    result = build_harness_plan(payload.prompt)
    result["tenantId"] = auth.tenant.id
    result["createdBy"] = auth.user.id
    return result
