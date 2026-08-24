#!/usr/bin/env python3
"""Queue and observe the canonical generated-Solution Phase 0 acceptance fixtures.

This script is intentionally a thin operator harness over the existing durable
Solution composer/worker lifecycle. It does not execute generated source itself and
does not bypass the worker, capability policy, model runtime, or isolated runner.

It is safe to rerun: the permanent attendance fixture is retried only when needed,
and sanity fixtures are reused by exact name when they already exist.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import desc, select

from packages.database.db import SessionFactory
from packages.database.product_models import SolutionJob, SolutionRecord
from packages.database.schema import import_all_models
from packages.solutions.composer import create_solution_from_intent, retry_solution_initial_generation
from packages.solutions.service import LifecycleStatus, RuntimeType, SolutionService


ATTENDANCE_SOLUTION_ID = os.getenv(
    "OPERLY_PHASE0_ATTENDANCE_SOLUTION_ID",
    "9aef3fee-d947-478c-8dc1-f80f6f30607e",
).strip()

SANITY_FIXTURES = (
    (
        "Phase 0 sanity — Equipment checkout tracker",
        "Build a private equipment checkout application. Employees authenticate, scan an equipment QR code to check an item out or return it, the checkout is persisted, and an owner-only view shows current and overdue checkouts. Reuse canonical workspace Employee identity where appropriate and generate executable acceptance tests.",
    ),
    (
        "Phase 0 sanity — Visitor sign-in log",
        "Build a private visitor sign-in application. A visitor records name and host employee, receives a QR-style visit badge, can check out later, visits are persisted, and an owner-only view shows who is currently onsite. Reuse canonical workspace Employee identity for hosts where appropriate and generate executable acceptance tests.",
    ),
)


@dataclass(frozen=True)
class Fixture:
    label: str
    solution_id: str


def _context(row: SolutionRecord) -> dict[str, Any]:
    try:
        value = json.loads(row.context_json or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


async def _latest_job(db, row: SolutionRecord) -> SolutionJob | None:
    return await db.scalar(
        select(SolutionJob)
        .where(SolutionJob.tenant_id == row.tenant_id, SolutionJob.solution_id == row.id)
        .order_by(desc(SolutionJob.attempt), desc(SolutionJob.created_at))
        .limit(1)
    )


async def _prepare() -> list[Fixture]:
    service = SolutionService()
    async with SessionFactory() as db:
        attendance = await db.get(SolutionRecord, ATTENDANCE_SOLUTION_ID)
        if attendance is None:
            raise RuntimeError(f"Permanent attendance fixture not found: {ATTENDANCE_SOLUTION_ID}")
        job = await _latest_job(db, attendance)
        user_id = str((job.created_by if job else None) or "").strip()
        if not user_id:
            raise RuntimeError("Attendance fixture has no creating principal to own acceptance retries")
        tenant_id = attendance.tenant_id

        if not (
            attendance.preview_state == "ready"
            and attendance.lifecycle_status == LifecycleStatus.PREVIEW_READY
        ):
            await retry_solution_initial_generation(
                db,
                tenant_id=tenant_id,
                user_id=user_id,
                solution_id=attendance.id,
                service=service,
            )
            await db.commit()

        fixtures = [Fixture("attendance", attendance.id)]
        for name, objective in SANITY_FIXTURES:
            row = await db.scalar(
                select(SolutionRecord)
                .where(SolutionRecord.tenant_id == tenant_id, SolutionRecord.name == name)
                .order_by(desc(SolutionRecord.created_at))
                .limit(1)
            )
            if row is None:
                row, decision = await create_solution_from_intent(
                    db,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    name=name,
                    objective=objective,
                    service=service,
                )
                if decision.runtime_type != RuntimeType.GENERATED_PROJECT:
                    raise RuntimeError(
                        f"Sanity fixture {name!r} resolved to {decision.runtime_type}, not generated_project"
                    )
                await db.commit()
            elif not (
                row.preview_state == "ready"
                and row.lifecycle_status == LifecycleStatus.PREVIEW_READY
            ):
                latest = await _latest_job(db, row)
                if latest is None or latest.status not in {"queued", "running"}:
                    await retry_solution_initial_generation(
                        db,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        solution_id=row.id,
                        service=service,
                    )
                    await db.commit()
            fixtures.append(Fixture(name, row.id))
        return fixtures


async def _snapshot(fixtures: list[Fixture]) -> tuple[bool, bool, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    all_terminal = True
    all_ready = True
    async with SessionFactory() as db:
        for fixture in fixtures:
            row = await db.get(SolutionRecord, fixture.solution_id)
            if row is None:
                rows.append({"label": fixture.label, "solutionId": fixture.solution_id, "missing": True})
                all_ready = False
                continue
            context = _context(row)
            generation = context.get("initialGeneration") if isinstance(context.get("initialGeneration"), dict) else {}
            job = await _latest_job(db, row)
            job_status = str(getattr(job, "status", "") or "")
            ready = row.preview_state == "ready" and row.lifecycle_status == LifecycleStatus.PREVIEW_READY
            terminal = ready or job_status == "failed" or row.lifecycle_status == LifecycleStatus.FAILED
            all_terminal = all_terminal and terminal
            all_ready = all_ready and ready
            rows.append(
                {
                    "label": fixture.label,
                    "solutionId": row.id,
                    "runtimeType": str(row.runtime_type),
                    "lifecycle": str(row.lifecycle_status),
                    "previewState": row.preview_state,
                    "stage": generation.get("stage"),
                    "stageStatus": generation.get("stageStatus"),
                    "attempt": generation.get("attempt") or getattr(job, "attempt", None),
                    "jobStatus": job_status or None,
                    "jobFailure": getattr(job, "failure_classification", None) if job else None,
                    "buildId": generation.get("buildId"),
                    "repairNumber": generation.get("repairNumber"),
                    "ready": ready,
                }
            )
    return all_terminal, all_ready, rows


async def main() -> int:
    import_all_models()
    fixtures = await _prepare()
    timeout = max(60.0, min(float(os.getenv("OPERLY_PHASE0_ACCEPTANCE_TIMEOUT_SECONDS", "1800")), 3600.0))
    interval = max(1.0, min(float(os.getenv("OPERLY_PHASE0_ACCEPTANCE_POLL_SECONDS", "3")), 30.0))
    deadline = time.monotonic() + timeout
    previous = None
    while True:
        terminal, ready, rows = await _snapshot(fixtures)
        encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True)
        if encoded != previous:
            print("PHASE0_ACCEPTANCE " + encoded, flush=True)
            previous = encoded
        if terminal:
            print(
                "PHASE0_ACCEPTANCE_SUMMARY "
                + json.dumps({"passed": ready, "fixtures": rows}, ensure_ascii=False, sort_keys=True),
                flush=True,
            )
            return 0 if ready else 1
        if time.monotonic() >= deadline:
            print(
                "PHASE0_ACCEPTANCE_SUMMARY "
                + json.dumps({"passed": False, "timedOut": True, "fixtures": rows}, ensure_ascii=False, sort_keys=True),
                flush=True,
            )
            return 2
        await asyncio.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
