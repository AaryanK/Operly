"""HTTP representation for durable managed-app generation failures."""
from fastapi.responses import JSONResponse

from packages.solutions.service import solution_json


def generation_failure_detail(row) -> dict:
    payload = solution_json(row)
    generation = payload.get("generation") or {}
    stage = generation.get("stage") or "initial_generation"
    reason = generation.get("error") or "Initial application generation failed safely."
    return {
        "code": "initial_generation_failed",
        "message": f"Application generation stopped at {stage}: {reason}",
        "failedStage": stage,
        "solution": payload,
        "retryEndpoint": f"/api/solutions/{row.id}/retry-generation",
        "traceEndpoint": f"/api/solutions/{row.id}/generation-trace",
    }


def generation_failure_response(row, *, status_code: int = 502) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": generation_failure_detail(row)},
    )
