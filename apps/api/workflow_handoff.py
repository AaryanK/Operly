"""Server-only wake signal from DragonZpyder; not a model-facing capability."""
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from packages.workflow.dispatch_driver import (
    DriverForbidden, DriverUnavailable, workflow_dispatch_driver,
)

router = APIRouter()


@router.post("/api/internal/dragonzpyder/workflow-pulse", include_in_schema=False)
async def dispatch_workflows(
    request: Request,
    proof: str | None = Header(default=None, alias="X-DragonZpyder-Workflow-Dispatch"),
):
    # Reject ambient browser credentials. The sole authority is the dedicated
    # service-to-service proof. Request carries no user-selected workflow or args.
    if request.headers.get("origin") is not None or request.headers.get("cookie") is not None:
        raise HTTPException(403, "server_only_workflow_dispatch")
    try:
        result = await workflow_dispatch_driver.remote_tick(proof)
    except DriverUnavailable as error:
        raise HTTPException(503, str(error)) from None
    except DriverForbidden:
        raise HTTPException(403, "workflow_driver_authentication_failed") from None
    except Exception:
        raise HTTPException(503, "workflow_dispatch_failed") from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
