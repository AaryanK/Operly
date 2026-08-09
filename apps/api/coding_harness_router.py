from fastapi import APIRouter, Depends, HTTPException

from apps.api.dependencies import AuthContext, get_auth_context
from packages.coding_harness.engine import build_harness_plan_with_model
from packages.coding_harness.model_resolution import CapabilityResolutionError
from packages.custom_software.schema import AgenticProjectInput
from packages.model_runtime import OllamaError

router = APIRouter(tags=["coding-harness"])


@router.post("/api/coding-harness/plans")
async def create_harness_plan(payload: AgenticProjectInput, auth: AuthContext = Depends(get_auth_context)):
    """Lower intent to reviewable IRs using model-resolved requirement knowledge.

    This endpoint never executes generated code. The model decides which existing
    coding-harness capabilities cover the request; OPERLY validates the decision
    and constructs the bounded IRs and tool policy.
    """
    try:
        result = await build_harness_plan_with_model(payload.prompt)
    except CapabilityResolutionError as error:
        raise HTTPException(status_code=502, detail={"code":"capability_resolution_failed","message":str(error)}) from error
    except OllamaError as error:
        raise HTTPException(status_code=503, detail=error.public_message) from error
    result["tenantId"] = auth.tenant.id
    result["createdBy"] = auth.user.id
    return result
