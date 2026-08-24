"""AI-Debug-correlated entrypoint for the durable generated-Solution worker.

This wrapper keeps the orchestration implementation in ``generation_worker`` while
binding one trace scope around the entire attempt, including planning, coding, runner
submission/polling, and the durable terminal job evidence. It is intentionally a
thin entrypoint so tracing cannot fork the worker lifecycle implementation.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from packages.database.model_trace import ensure_model_trace_sink
from packages.model_runtime.trace_context import RuntimeTraceEvent, emit_runtime_trace_event, runtime_trace_scope
from packages.solutions import generation_worker as worker

_ORIGINAL_PROCESS_GENERATION_JOB = worker.process_generation_job


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _metadata(job, user_id: str) -> dict[str, Any]:
    return {
        "conversation_id": f"solution:{job.solution_id}",
        "runtime_run_id": f"solution:{job.solution_id}:attempt:{max(1, int(job.attempt or 1))}",
        "tenant_id": job.tenant_id,
        "user_id": user_id,
        "principal_id": f"user:{user_id}",
        "channel": "solution",
        "surface": "solution_generation",
        "runtime_component": "solution_worker",
        "solution_id": job.solution_id,
        "solution_job_id": job.id,
        "generation_attempt": max(1, int(job.attempt or 1)),
    }


async def _event(event_type: str, payload: Any = None, *, phase: str = "event", classification: str | None = None, retryable: bool | None = None) -> None:
    await emit_runtime_trace_event(
        RuntimeTraceEvent(
            event_type=event_type,
            payload=payload,
            phase=phase,
            resource_id="solution_worker",
            classification=classification,
            retryable=retryable,
        )
    )


async def traced_process_generation_job(db, job) -> None:
    evidence = _json_object(job.evidence_json)
    user_id = str(job.created_by or evidence.get("createdBy") or "").strip()
    if not user_id:
        return await _ORIGINAL_PROCESS_GENERATION_JOB(db, job)

    with runtime_trace_scope(_metadata(job, user_id)):
        await _event(
            "solution_job.started",
            {
                "jobId": job.id,
                "solutionId": job.solution_id,
                "attempt": job.attempt,
                "idempotencyKey": job.idempotency_key,
                "sourceVersionReference": job.source_version_reference,
                "existingLog": _json_list(job.log_json),
                "evidence": evidence,
            },
        )
        try:
            await _ORIGINAL_PROCESS_GENERATION_JOB(db, job)
            await db.refresh(job)
            terminal_payload = {
                "jobId": job.id,
                "solutionId": job.solution_id,
                "status": job.status,
                "attempt": job.attempt,
                "planId": job.plan_id,
                "sourceVersionReference": job.source_version_reference,
                "failureClassification": job.failure_classification,
                "log": _json_list(job.log_json),
                "evidence": _json_object(job.evidence_json),
            }
            if job.status == "succeeded":
                await _event("solution_job.completed", terminal_payload, phase="success")
            elif job.status == "failed":
                await _event(
                    "solution_job.failed",
                    terminal_payload,
                    phase="error",
                    classification=job.failure_classification or "solution_generation_failed",
                    retryable=True,
                )
            else:
                await _event("solution_job.returned", terminal_payload)
        except Exception as error:
            await _event(
                "solution_job.exception",
                {"jobId": job.id, "type": type(error).__name__, "message": str(error)},
                phase="error",
                classification=type(error).__name__,
                retryable=True,
            )
            raise


async def run_forever() -> None:
    ensure_model_trace_sink()
    worker.process_generation_job = traced_process_generation_job
    await worker.run_forever()


if __name__ == "__main__":
    asyncio.run(run_forever())
