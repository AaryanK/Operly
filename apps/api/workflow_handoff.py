"""Server-only compatibility dispatch; not a model-facing capability."""
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from packages.workflow.dispatch_driver import (
    DriverBusy, DriverForbidden, DriverUnavailable, workflow_dispatch_driver,
)

router = APIRouter()


@router.post("/api/internal/dragonzpyder/workflow-dispatch", include_in_schema=False)
async def dispatch_workflows(
    request: Request,
    proof: str | None = Header(default=None, alias="X-DragonZpyder-Workflow-Proof"),
):
    # No browser credentials, selected user IDs, workflow IDs, tokens or
    # arguments enter the legacy scheduler through this transport.
    if request.headers.get("origin") is not None or request.headers.get("cookie") is not None:
        raise HTTPException(403, "server_only_workflow_dispatch")
    try:
        result = await workflow_dispatch_driver.remote_tick(proof)
    except DriverUnavailable as error:
        raise HTTPException(503, str(error)) from None
    except DriverForbidden as error:
        raise HTTPException(403, str(error)) from None
    except DriverBusy as error:
        raise HTTPException(409, str(error)) from None
    except Exception:
        raise HTTPException(503, "workflow_dispatch_failed") from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
