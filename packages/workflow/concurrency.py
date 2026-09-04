from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.db import session_scope
from packages.kernel.contracts import CapabilityExecutionResult, CapabilitySpec
from packages.security.execution_context import ExecutionContext
from packages.workflow.access import WorkflowProvider as AccessWorkflowProvider
from packages.workflow.models import WorkflowDefinition, WorkflowRun, WorkflowVersion
from packages.workflow.scheduler import WorkflowScheduler
from packages.workflow.tracing import record_workflow_event


DEFAULT_CONCURRENCY_POLICY: dict[str, Any] = {
    "max_concurrent_runs": 0,
    "overflow_policy": "queue",
}
MAX_CONCURRENT_RUNS = 64
OCCUPYING_RUN_STATES = frozenset({"running", "waiting", "waiting_approval"})


def _loads(raw: str | None, fallback: Any) -> Any:
    try:
        return json.loads(raw or "")
    except Exception:
        return fallback


def _dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def normalize_concurrency_policy(value: Any) -> dict[str, Any]:
    """Return the canonical per-workflow run concurrency policy.

    ``max_concurrent_runs=0`` preserves Operly's historical behavior: no per-workflow
    cap beyond the scheduler's global worker bound. Positive values cap simultaneous
    active runs for one workflow. Durable waits and approval pauses occupy a slot.
    """

    if value is None:
        return dict(DEFAULT_CONCURRENCY_POLICY)
    if not isinstance(value, dict):
        raise ValueError("Workflow concurrency must be an object or null")

    unknown = set(value) - {"max_concurrent_runs", "overflow_policy"}
    if unknown:
        raise ValueError(
            "Unsupported Workflow concurrency field(s): " + ", ".join(sorted(unknown))
        )

    raw_limit = value.get("max_concurrent_runs", 0)
    if isinstance(raw_limit, bool):
        raise ValueError("max_concurrent_runs must be an integer")
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError) as error:
        raise ValueError("max_concurrent_runs must be an integer") from error
    if limit < 0 or limit > MAX_CONCURRENT_RUNS:
        raise ValueError(
            f"max_concurrent_runs must be between 0 and {MAX_CONCURRENT_RUNS}"
        )

    overflow = str(value.get("overflow_policy") or "queue").strip().lower()
    if overflow not in {"queue", "drop"}:
        raise ValueError("overflow_policy must be 'queue' or 'drop'")
    if limit == 0:
        # Drop has no meaning without a limit. Canonicalize it away so snapshots and
        # traces cannot imply suppression when the workflow is actually unlimited.
        overflow = "queue"

    return {
        "max_concurrent_runs": limit,
        "overflow_policy": overflow,
    }


def policy_from_snapshot(snapshot: Any) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return dict(DEFAULT_CONCURRENCY_POLICY)
    try:
        return normalize_concurrency_policy(snapshot.get("concurrency"))
    except ValueError:
        # Preserve the explicit pre-feature behavior for historical malformed metadata
        # rather than making dispatch depend on an unreadable policy blob.
        return dict(DEFAULT_CONCURRENCY_POLICY)


def _concurrency_schema() -> dict[str, Any]:
    return {
        "type": ["object", "null"],
        "properties": {
            "max_concurrent_runs": {
                "type": "integer",
                "minimum": 0,
                "maximum": MAX_CONCURRENT_RUNS,
            },
            "overflow_policy": {
                "type": "string",
                "enum": ["queue", "drop"],
            },
        },
        "additionalProperties": False,
    }


def extend_workflow_capabilities(
    specs: tuple[CapabilitySpec, ...],
) -> tuple[CapabilitySpec, ...]:
    """Add concurrency policy to create/update without inventing a second API family."""

    extended: list[CapabilitySpec] = []
    for spec in specs:
        if spec.id not in {"workflow.create", "workflow.update"}:
            extended.append(spec)
            continue
        schema = copy.deepcopy(spec.input_schema)
        properties = schema.setdefault("properties", {})
        properties["concurrency"] = _concurrency_schema()
        extended.append(replace(spec, version="1.2.0", input_schema=schema))
    return tuple(extended)


def _version_payload(row: WorkflowVersion) -> dict[str, Any]:
    return {
        "id": row.id,
        "workflow_id": row.workflow_id,
        "version": row.version,
        "created_by_user_id": row.created_by_user_id,
        "created_at": row.created_at.isoformat(),
        "spec": _loads(row.spec_json, {}),
        "snapshot": _loads(row.snapshot_json, {}),
    }


class WorkflowProvider(AccessWorkflowProvider):
    """Universal Workflow provider plus immutable concurrency policy snapshots.

    Concurrency is an operational dispatch policy stored in each immutable workflow
    version snapshot. ``workflow.update`` preserves the prior policy unless a new
    ``concurrency`` value is supplied. Existing workflows read as unlimited/queue.
    """

    async def _authorized_workflow(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        workflow_id: str,
        *,
        lock: bool = False,
    ) -> WorkflowDefinition:
        if context.is_personal or context.role == "owner":
            row = await self._workflow(db, context, workflow_id)
        else:
            row = await self._owned_workflow(db, context, workflow_id)
        if not lock:
            return row
        locked = await db.scalar(
            select(WorkflowDefinition)
            .where(WorkflowDefinition.id == row.id)
            .with_for_update()
        )
        if locked is None:
            raise LookupError("Workflow is unavailable")
        return locked

    async def _current_version(
        self,
        db: AsyncSession,
        workflow: WorkflowDefinition,
    ) -> WorkflowVersion:
        version = await db.scalar(
            select(WorkflowVersion).where(
                WorkflowVersion.workflow_id == workflow.id,
                WorkflowVersion.version == workflow.current_version,
            )
        )
        if version is None:
            raise RuntimeError("Current workflow version is unavailable")
        return version

    async def _current_policy(
        self,
        db: AsyncSession,
        workflow: WorkflowDefinition,
    ) -> dict[str, Any]:
        version = await self._current_version(db, workflow)
        return policy_from_snapshot(_loads(version.snapshot_json, {}))

    async def _store_current_policy(
        self,
        db: AsyncSession,
        workflow: WorkflowDefinition,
        policy: dict[str, Any],
    ) -> WorkflowVersion:
        version = await self._current_version(db, workflow)
        snapshot = _loads(version.snapshot_json, {})
        if not isinstance(snapshot, dict):
            snapshot = {}
        snapshot["concurrency"] = dict(policy)
        version.snapshot_json = _dumps(snapshot)
        return version

    async def _create_concurrency_only_version(
        self,
        db: AsyncSession,
        *,
        workflow: WorkflowDefinition,
        policy: dict[str, Any],
        created_by_user_id: str | None,
    ) -> tuple[WorkflowVersion, WorkflowVersion]:
        previous = await self._current_version(db, workflow)
        snapshot = _loads(previous.snapshot_json, {})
        if not isinstance(snapshot, dict):
            snapshot = {}
        snapshot["concurrency"] = dict(policy)
        workflow.current_version += 1
        version = WorkflowVersion(
            workflow_id=workflow.id,
            version=workflow.current_version,
            spec_json=previous.spec_json,
            snapshot_json=_dumps(snapshot),
            created_by_user_id=created_by_user_id,
        )
        db.add(version)
        await db.flush()
        return previous, version

    @staticmethod
    def _decorate_value(
        value: dict[str, Any],
        policy: dict[str, Any] | None = None,
    ) -> None:
        version = value.get("version")
        snapshot = version.get("snapshot") if isinstance(version, dict) else None
        if policy is None:
            policy = policy_from_snapshot(snapshot)
        if isinstance(snapshot, dict):
            snapshot["concurrency"] = dict(policy)
        workflow = value.get("workflow")
        if isinstance(workflow, dict):
            workflow["concurrency"] = dict(policy)

    async def execute(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
        arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        capability_id = capability.id
        concurrency_supplied = (
            capability_id in {"workflow.create", "workflow.update"}
            and "concurrency" in arguments
        )
        requested_policy = (
            normalize_concurrency_policy(arguments.get("concurrency"))
            if concurrency_supplied
            else None
        )

        previous_policy: dict[str, Any] | None = None
        previous_version_number: int | None = None
        locked_workflow: WorkflowDefinition | None = None
        if capability_id == "workflow.update":
            locked_workflow = await self._authorized_workflow(
                db,
                context,
                str(arguments.get("workflow_id") or ""),
                lock=True,
            )
            previous_policy = await self._current_policy(db, locked_workflow)
            previous_version_number = locked_workflow.current_version

        concurrency_changed = bool(
            concurrency_supplied
            and requested_policy != (previous_policy or dict(DEFAULT_CONCURRENCY_POLICY))
        )

        provider_arguments = dict(arguments)
        provider_arguments.pop("concurrency", None)
        result = await super().execute(
            db,
            context=context,
            capability=capability,
            arguments=provider_arguments,
            minimum_context=minimum_context,
        )

        if capability_id in {"workflow.create", "workflow.update"}:
            workflow_id = str(result.resource_id or "")
            workflow = locked_workflow or await self._authorized_workflow(
                db, context, workflow_id, lock=True
            )
            if concurrency_supplied:
                policy = requested_policy or dict(DEFAULT_CONCURRENCY_POLICY)
            elif capability_id == "workflow.update":
                policy = previous_policy or dict(DEFAULT_CONCURRENCY_POLICY)
            else:
                policy = dict(DEFAULT_CONCURRENCY_POLICY)

            created_concurrency_only_version = False
            previous_version: WorkflowVersion | None = None
            if (
                capability_id == "workflow.update"
                and concurrency_changed
                and workflow.current_version == previous_version_number
            ):
                previous_version, version = await self._create_concurrency_only_version(
                    db,
                    workflow=workflow,
                    policy=policy,
                    created_by_user_id=context.user_id,
                )
                created_concurrency_only_version = True
            elif capability_id == "workflow.create" or (
                capability_id == "workflow.update"
                and workflow.current_version != previous_version_number
            ):
                # The version was created in this same transaction by the base
                # provider, so adding concurrency metadata here does not mutate any
                # previously visible immutable snapshot.
                version = await self._store_current_policy(db, workflow, policy)
            else:
                # No ordinary fields changed and the requested concurrency policy was
                # identical to the existing one. Do not create or mutate a version.
                version = await self._current_version(db, workflow)

            if isinstance(result.value, dict):
                if created_concurrency_only_version:
                    result.value["version"] = _version_payload(version)
                    result.value["changed"] = ["concurrency"]
                    workflow_payload = result.value.get("workflow")
                    if isinstance(workflow_payload, dict):
                        workflow_payload["current_version"] = workflow.current_version
                elif concurrency_changed:
                    changed = result.value.get("changed")
                    if isinstance(changed, list) and "concurrency" not in changed:
                        changed.append("concurrency")
                self._decorate_value(result.value, policy)

            if created_concurrency_only_version:
                await record_workflow_event(
                    db,
                    workspace_id=context.workspace_id,
                    workflow_id=workflow.id,
                    event_type="workflow.updated",
                    actor_type="human",
                    actor_id=context.user_id,
                    owner_user_id=workflow.owner_user_id,
                    principal_id=context.principal_id,
                    payload={
                        "version": workflow.current_version,
                        "version_id": version.id,
                        "previous_version_id": previous_version.id if previous_version else None,
                        "changed": ["concurrency"],
                    },
                )
                result = CapabilityExecutionResult(
                    value=result.value,
                    resource_type=result.resource_type,
                    resource_id=result.resource_id,
                    event_payload={
                        "workflow_id": workflow.id,
                        "workflow_version_id": version.id,
                    },
                )

            if concurrency_changed or (
                capability_id == "workflow.create" and concurrency_supplied
            ):
                await record_workflow_event(
                    db,
                    workspace_id=context.workspace_id,
                    workflow_id=workflow.id,
                    event_type="workflow.concurrency.updated",
                    actor_type="human",
                    actor_id=context.user_id,
                    owner_user_id=workflow.owner_user_id,
                    principal_id=context.principal_id,
                    payload={
                        "workflow_version": workflow.current_version,
                        "workflow_version_id": version.id,
                        "max_concurrent_runs": policy["max_concurrent_runs"],
                        "overflow_policy": policy["overflow_policy"],
                    },
                )
            return result

        if capability_id == "workflow.get" and isinstance(result.value, dict):
            self._decorate_value(result.value)
        elif capability_id == "workflow.version.get" and isinstance(result.value, dict):
            version_payload = result.value.get("version")
            if isinstance(version_payload, dict):
                snapshot = version_payload.get("snapshot")
                if isinstance(snapshot, dict):
                    snapshot["concurrency"] = policy_from_snapshot(snapshot)
        return result


async def _workflow_policy(
    db: AsyncSession,
    workflow: WorkflowDefinition,
) -> dict[str, Any]:
    version = await db.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == workflow.id,
            WorkflowVersion.version == workflow.current_version,
        )
    )
    if version is None:
        return dict(DEFAULT_CONCURRENCY_POLICY)
    return policy_from_snapshot(_loads(version.snapshot_json, {}))


class ConcurrentWorkflowScheduler(WorkflowScheduler):
    """HA scheduler with durable per-workflow run concurrency.

    The WorkflowDefinition row is the cross-replica mutex. A scheduler locks one
    workflow before computing its occupied slots, so two replicas cannot both consume
    the last slot. Process-local worker counts remain only the global resource bound.
    """

    def status(self) -> dict[str, object]:
        value = dict(super().status())
        value.update(
            {
                "per_workflow_concurrency": True,
                "concurrency_policy_source": "workflow_version_snapshot",
                "max_configurable_concurrent_runs": MAX_CONCURRENT_RUNS,
            }
        )
        return value

    async def _suppress_overflow_run(
        self,
        db: AsyncSession,
        *,
        workflow: WorkflowDefinition,
        run: WorkflowRun,
        policy: dict[str, Any],
        now: datetime,
    ) -> None:
        del workflow
        run.status = "cancelled"
        run.error_code = "concurrency_suppressed"
        run.error_message = (
            "This run was suppressed because the workflow concurrency limit was full."
        )
        run.finished_at = now
        run.lease_token = None
        run.lease_until = None
        await record_workflow_event(
            db,
            workspace_id=run.workspace_id,
            workflow_id=run.workflow_id,
            workflow_run_id=run.id,
            event_type="workflow.run.suppressed",
            actor_type="system",
            actor_id="operly:scheduler",
            owner_user_id=run.authority_user_id,
            principal_id=(
                f"user:{run.authority_user_id}" if run.authority_user_id else None
            ),
            payload={
                "reason": "concurrency_limit",
                "max_concurrent_runs": policy["max_concurrent_runs"],
                "overflow_policy": policy["overflow_policy"],
                "trigger_type": run.trigger_type,
            },
        )

    async def _claim_runs(self, *, limit: int) -> list[tuple[str, str]]:
        if limit < 1:
            return []
        now = datetime.utcnow()
        unleased = or_(
            WorkflowRun.lease_until.is_(None),
            WorkflowRun.lease_until < now,
        )
        oldest = func.min(WorkflowRun.created_at).label("oldest")

        async with session_scope() as db:
            # Start from workflows rather than a flat run prefix. A noisy serial
            # workflow therefore cannot hide every other workflow behind thousands of
            # queued rows.
            workflow_candidates = (
                await db.execute(
                    select(WorkflowRun.workflow_id, oldest)
                    .where(WorkflowRun.status == "queued", unleased)
                    .group_by(WorkflowRun.workflow_id)
                    .order_by(oldest.asc())
                    .limit(max(25, min(limit * 8, 256)))
                )
            ).all()

            claims: list[tuple[str, str]] = []
            for workflow_id, _oldest_at in workflow_candidates:
                if len(claims) >= limit:
                    break

                # This row lock is the per-workflow cross-replica dispatch mutex.
                # skip_locked avoids scheduler-to-scheduler waiting/deadlock; another
                # replica holding the workflow simply gets this tick.
                workflow = await db.scalar(
                    select(WorkflowDefinition)
                    .where(WorkflowDefinition.id == workflow_id)
                    .with_for_update(skip_locked=True)
                )
                if workflow is None:
                    continue

                policy = await _workflow_policy(db, workflow)
                max_runs = int(policy["max_concurrent_runs"])
                global_remaining = limit - len(claims)

                active = int(
                    await db.scalar(
                        select(func.count(WorkflowRun.id)).where(
                            WorkflowRun.workflow_id == workflow.id,
                            or_(
                                WorkflowRun.status.in_(tuple(OCCUPYING_RUN_STATES)),
                                and_(
                                    WorkflowRun.status == "queued",
                                    WorkflowRun.lease_until.is_not(None),
                                    WorkflowRun.lease_until >= now,
                                ),
                            ),
                        )
                    )
                    or 0
                )
                per_workflow_available = (
                    global_remaining
                    if max_runs == 0
                    else max(0, max_runs - active)
                )

                # Fetch enough overflow rows to make drop-policy behavior visible in
                # one tick, while keeping a hard bound for pathological trigger storms.
                scan_limit = max(50, min(512, global_remaining * 16))
                queued = (
                    await db.scalars(
                        select(WorkflowRun)
                        .where(
                            WorkflowRun.workflow_id == workflow.id,
                            WorkflowRun.status == "queued",
                            unleased,
                        )
                        .order_by(WorkflowRun.created_at.asc(), WorkflowRun.id.asc())
                        .limit(scan_limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()

                claim_budget = min(global_remaining, per_workflow_available)
                for index, run in enumerate(queued):
                    if index < claim_budget:
                        token = str(uuid4())
                        run.lease_token = token
                        run.lease_until = now + timedelta(seconds=self._lease_seconds)
                        claims.append((run.id, token))
                        continue

                    exceeds_workflow_limit = (
                        max_runs > 0 and index >= per_workflow_available
                    )
                    if (
                        exceeds_workflow_limit
                        and policy["overflow_policy"] == "drop"
                    ):
                        await self._suppress_overflow_run(
                            db,
                            workflow=workflow,
                            run=run,
                            policy=policy,
                            now=now,
                        )
                    # If only the *global* worker bound is full, leave the run queued.

            return claims


def install_concurrency_scheduler() -> ConcurrentWorkflowScheduler:
    """Replace the legacy module singleton while preserving every import path."""

    from packages.workflow import scheduler as scheduler_module

    scheduler = ConcurrentWorkflowScheduler()
    scheduler_module.workflow_scheduler = scheduler
    return scheduler
