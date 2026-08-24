from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.task_provider import (
    TaskProvider,
    dump_task_payload,
    load_task_payload,
)
from packages.database.models import ScheduledJob, Task
from packages.plugins import default_plugin_runtime
from packages.tasks.workflow import WorkflowValidationError, validate_workflow


_TRIGGER_KINDS = {"once", "daily", "interval", "monitor", "event"}

_WORKFLOW_SCHEMA = {
    "type": "object",
    "description": (
        "Validated declarative workflow. Use for multi-step durable logic instead of putting control flow only in the objective. "
        "Supported node types are invoke, model, if, foreach, set, emit, stop. invoke nodes name canonical Operly capabilities; "
        "model nodes are bounded model.invoke calls. Values may reference earlier outputs with $node.path, $trigger.path, $state.path, or foreach aliases."
    ),
    "properties": {
        "version": {"type": "integer", "minimum": 1},
        "steps": {
            "type": "array",
            "minItems": 1,
            "maxItems": 80,
            "items": {"type": "object"},
        },
    },
    "required": ["steps"],
}

_TRIGGER_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["once", "daily", "interval", "monitor", "event"]},
        "run_at": {"type": "string", "description": "Initial ISO-8601 instant with offset when supplied."},
        "every_minutes": {"type": "integer", "minimum": 5, "maximum": 10080},
        "url": {"type": "string", "maxLength": 2048},
        "timezone": {
            "type": "string",
            "maxLength": 100,
            "description": "IANA timezone such as Europe/Helsinki or Asia/Kathmandu. Explicit user timezone wins over actor/workspace defaults.",
        },
        "local_time": {
            "type": "string",
            "description": "Wall-clock HH:MM[:SS] for daily schedules. Persisted with timezone so DST is handled correctly.",
        },
        "event_id": {
            "type": "string",
            "maxLength": 200,
            "description": "Exact plugin event id discovered with event.search/event.describe. Never invent event ids.",
        },
        "where": {
            "type": "object",
            "description": "Optional exact-match filter over event envelope/payload dotted paths.",
        },
    },
    "required": ["kind"],
}


def _enhanced_create() -> CapabilityDefinition:
    return CapabilityDefinition(
        "task.create",
        "task_create",
        (
            "Create a durable personal/workspace task. For simple work, persist an objective with once/daily/interval/monitor trigger. "
            "For multi-step autonomous work, compile the user's natural-language request into workflow. Use capability.search/describe for operations and "
            "event.search/describe for event triggers; do not invent capabilities or events. Daily schedules must preserve an explicit or actor IANA timezone. "
            "Event-triggered tasks subscribe to plugin-declared workspace events and still execute every action through the existing capability harness/firewall."
        ),
        {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 500},
                "objective": {"type": "string", "minLength": 1, "maxLength": 12000},
                "trigger": _TRIGGER_SCHEMA,
                "workflow": _WORKFLOW_SCHEMA,
                "delivery": {"type": "string", "enum": ["origin", "dm", "channel"]},
            },
            "required": ["title", "objective", "trigger"],
            "additionalProperties": False,
        },
        {"type": "object"},
        risk_level="low",
        permissions=("tasks:write",),
        approval_policy=ApprovalPolicy.AUTO,
        reversible=True,
        category="tasks",
        tags=frozenset({"tasks", "workflow", "automation", "durable"}),
        semantic_operations=frozenset({"create workflow", "schedule task", "subscribe to event"}),
    )


def _enhanced_update() -> CapabilityDefinition:
    return CapabilityDefinition(
        "task.update",
        "task_update",
        "Edit a durable task, including its trigger or validated workflow. Event ids must come from event discovery. Time changes preserve IANA timezone semantics.",
        {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "minLength": 1},
                "title": {"type": "string", "minLength": 1, "maxLength": 500},
                "objective": {"type": "string", "minLength": 1, "maxLength": 12000},
                "status": {"type": "string", "enum": ["open", "paused"]},
                "trigger": _TRIGGER_SCHEMA,
                "workflow": _WORKFLOW_SCHEMA,
                "delivery": {"type": "string", "enum": ["origin", "dm", "channel"]},
            },
            "required": ["task_id"],
            "additionalProperties": False,
        },
        {"type": "object"},
        risk_level="low",
        permissions=("tasks:write",),
        approval_policy=ApprovalPolicy.AUTO,
        reversible=True,
        category="tasks",
    )


class WorkflowTaskProvider(TaskProvider):
    """Backward-compatible TaskProvider with plugin-event/workflow composition."""

    capabilities = tuple(
        definition
        for definition in TaskProvider.capabilities
        if definition.id not in {"task.create", "task.update"}
    ) + (_enhanced_create(), _enhanced_update())

    @staticmethod
    def _event_trigger(trigger: dict) -> dict:
        event_id = str(trigger.get("event_id") or "").strip()
        if not event_id:
            raise ValueError("event_id_required")
        try:
            event = default_plugin_runtime().manifests.event(event_id)
        except LookupError as error:
            raise ValueError("event_not_registered_use_event_search") from error
        if event.scope == "personal":
            raise ValueError("personal_event_store_not_supported_yet")
        where = trigger.get("where") or {}
        if not isinstance(where, dict) or len(where) > 20:
            raise ValueError("event_filter_invalid")
        return {
            "kind": "event",
            "event_id": event_id,
            "where": where,
            "event_plugin_id": default_plugin_runtime().manifests.owner_for_event(event_id),
        }

    @staticmethod
    def _prepare_daily_trigger(trigger: dict, temporal: dict | None) -> tuple[dict, str | None, str | None]:
        if str(trigger.get("kind") or "").lower() != "daily":
            return dict(trigger), None, None
        temporal = temporal if isinstance(temporal, dict) else {}
        timezone_name = str(
            trigger.get("timezone")
            or temporal.get("actor_timezone")
            or temporal.get("workspace_timezone")
            or "UTC"
        ).strip()
        try:
            zone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("invalid_schedule_timezone") from error
        local_time = str(trigger.get("local_time") or "").strip()
        run_at = str(trigger.get("run_at") or "").strip()
        prepared = dict(trigger)
        if local_time and not run_at:
            try:
                hour, minute, *seconds = [int(part) for part in local_time.split(":")]
                second = seconds[0] if seconds else 0
                now_local = datetime.now(timezone.utc).astimezone(zone)
                candidate = now_local.replace(hour=hour, minute=minute, second=second, microsecond=0)
                if candidate <= now_local:
                    candidate += timedelta(days=1)
                prepared["run_at"] = candidate.isoformat()
            except (ValueError, TypeError) as error:
                raise ValueError("invalid_local_time") from error
        elif run_at:
            try:
                parsed = datetime.fromisoformat(run_at.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError
                local_time = parsed.astimezone(zone).timetz().replace(tzinfo=None).isoformat()
            except ValueError as error:
                raise ValueError("invalid_run_at") from error
        prepared.pop("timezone", None)
        prepared.pop("local_time", None)
        return prepared, timezone_name, local_time or None

    async def _enrich(self, context, result: CapabilityResult) -> CapabilityResult:
        if not result.success:
            return result
        evidence = dict(result.evidence)
        rows = []
        if isinstance(evidence.get("task"), dict):
            rows = [evidence["task"]]
        elif isinstance(evidence.get("tasks"), list):
            rows = [row for row in evidence["tasks"] if isinstance(row, dict)]
        for row in rows:
            task_id = str(row.get("id") or "")
            job = await self._job(context, task_id) if task_id else None
            payload = load_task_payload(job.content if job else None)
            row["workflow"] = payload.get("workflow")
            row["workflow_state"] = payload.get("state") or {}
            row["trigger"] = payload.get("trigger", row.get("trigger"))
            row["objective"] = payload.get("objective", row.get("objective"))
        return CapabilityResult(result.success, result.changed, evidence, result.external_reference)

    async def execute(self, context, capability_name, arguments):
        if capability_name in {"task.get", "task.list"}:
            return await self._enrich(context, await super().execute(context, capability_name, arguments))

        if capability_name == "task.cancel":
            task = await self._authorized_task(context, str(arguments.get("task_id") or ""))
            job = await self._job(context, task.id) if task else None
            if task and job and job.status == "waiting_event":
                task.status = "cancelled"
                job.status = "cancelled"
                await context.db.flush()
                return CapabilityResult(True, True, {"task": self._task_view(task, job)}, task.id)
            return await super().execute(context, capability_name, arguments)

        if capability_name == "task.create":
            workflow = arguments.get("workflow")
            try:
                workflow = validate_workflow(workflow)
            except WorkflowValidationError as error:
                return CapabilityResult(False, False, {"reason": str(error)})
            invocation, metadata = self._invocation(context)
            trigger_input = arguments.get("trigger") if isinstance(arguments.get("trigger"), dict) else {}
            kind = str(trigger_input.get("kind") or "").lower()
            if kind not in _TRIGGER_KINDS:
                return CapabilityResult(False, False, {"reason": "unsupported_trigger_kind"})

            if kind == "event":
                if self._personal_scope(context):
                    return CapabilityResult(False, False, {"reason": "personal_event_store_not_supported_yet"})
                origin = await self._discord_origin(context)
                if origin is None:
                    return CapabilityResult(False, False, {"reason": "discord_origin_required_for_current_delivery_adapter"})
                try:
                    trigger = self._event_trigger(trigger_input)
                except ValueError as error:
                    return CapabilityResult(False, False, {"reason": str(error)})
                title = " ".join(str(arguments.get("title") or "").split())[:500]
                objective = str(arguments.get("objective") or "").strip()[:12000]
                if not title or not objective or not context.tenant_id:
                    return CapabilityResult(False, False, {"reason": "title_objective_workspace_required"})
                delivery = str(arguments.get("delivery") or "origin").lower()
                if delivery == "origin":
                    delivery = "channel"
                payload = {
                    "version": 2,
                    "objective": objective,
                    "trigger": trigger,
                    "workflow": workflow,
                    "delivery": delivery,
                    "origin": origin,
                    "state": {},
                    "event_queue": [],
                }
                task = Task(
                    tenant_id=context.tenant_id,
                    owner_user_id=context.actor_id,
                    guild_id=int(origin["external_space_id"]) if origin["external_space_id"] else None,
                    channel_id=int(origin["external_conversation_id"]),
                    creator_id=int(origin["external_user_id"]),
                    title=title,
                    status="open",
                    due_at=None,
                )
                context.db.add(task)
                await context.db.flush()
                job = ScheduledJob(
                    tenant_id=context.tenant_id,
                    task_id=task.id,
                    guild_id=task.guild_id,
                    channel_id=int(origin["external_conversation_id"]),
                    user_id=int(origin["external_user_id"]),
                    job_type="task",
                    content=dump_task_payload(payload),
                    delivery=delivery,
                    run_at=datetime.utcnow(),
                    status="waiting_event",
                )
                context.db.add(job)
                await context.db.flush()
                return CapabilityResult(
                    True,
                    True,
                    {"task": {**self._task_view(task, job), "workflow": workflow}},
                    task.id,
                )

            prepared = dict(arguments)
            try:
                prepared_trigger, explicit_zone, explicit_local = self._prepare_daily_trigger(
                    trigger_input,
                    invocation.get("temporal_context") or metadata.get("temporal_context"),
                )
            except ValueError as error:
                return CapabilityResult(False, False, {"reason": str(error)})
            prepared["trigger"] = prepared_trigger
            prepared.pop("workflow", None)
            result = await super().execute(context, capability_name, prepared)
            if not result.success:
                return result
            job = await self._job(context, str(result.external_reference or ""))
            if job:
                payload = load_task_payload(job.content)
                payload["version"] = 2
                payload["workflow"] = workflow
                if explicit_zone:
                    payload["trigger"]["timezone"] = explicit_zone
                if explicit_local:
                    payload["trigger"]["local_time"] = explicit_local
                job.content = dump_task_payload(payload)
                await context.db.flush()
            return await self._enrich(context, result)

        if capability_name == "task.update":
            task = await self._authorized_task(context, str(arguments.get("task_id") or ""))
            if task is None:
                return CapabilityResult(False, False, {"reason": "task_not_found_or_not_authorized"})
            job = await self._job(context, task.id)
            if job is None:
                return CapabilityResult(False, False, {"reason": "task_job_missing"})
            payload = load_task_payload(job.content)
            current_trigger = payload.get("trigger") if isinstance(payload.get("trigger"), dict) else {}
            new_workflow = arguments.get("workflow", payload.get("workflow"))
            try:
                new_workflow = validate_workflow(new_workflow) if new_workflow is not None else None
            except WorkflowValidationError as error:
                return CapabilityResult(False, False, {"reason": str(error)})
            trigger_input = arguments.get("trigger") if isinstance(arguments.get("trigger"), dict) else None
            target_kind = str((trigger_input or current_trigger).get("kind") or "").lower()

            if target_kind == "event":
                if self._personal_scope(context):
                    return CapabilityResult(False, False, {"reason": "personal_event_store_not_supported_yet"})
                try:
                    event_trigger = self._event_trigger(trigger_input or current_trigger)
                except ValueError as error:
                    return CapabilityResult(False, False, {"reason": str(error)})
                if arguments.get("title") is not None:
                    title = " ".join(str(arguments.get("title") or "").split())[:500]
                    if not title:
                        return CapabilityResult(False, False, {"reason": "title_required"})
                    task.title = title
                if arguments.get("objective") is not None:
                    objective = str(arguments.get("objective") or "").strip()[:12000]
                    if not objective:
                        return CapabilityResult(False, False, {"reason": "objective_required"})
                    payload["objective"] = objective
                if arguments.get("delivery") is not None:
                    delivery = str(arguments.get("delivery") or "origin").lower()
                    if delivery == "origin":
                        delivery = "channel"
                    if delivery not in {"channel", "dm"}:
                        return CapabilityResult(False, False, {"reason": "unsupported_delivery"})
                    payload["delivery"] = delivery
                    job.delivery = delivery
                if arguments.get("status") is not None:
                    task.status = str(arguments.get("status") or "").lower()
                payload["trigger"] = event_trigger
                payload["workflow"] = new_workflow
                if trigger_input is not None:
                    payload["state"] = {}
                    payload["event_queue"] = []
                job.content = dump_task_payload(payload)
                task.due_at = None
                job.status = "paused" if task.status == "paused" else "waiting_event"
                await context.db.flush()
                return CapabilityResult(
                    True,
                    True,
                    {"task": {**self._task_view(task, job), "workflow": new_workflow}},
                    task.id,
                )

            invocation, metadata = self._invocation(context)
            prepared = dict(arguments)
            explicit_zone = explicit_local = None
            if trigger_input is not None:
                try:
                    prepared_trigger, explicit_zone, explicit_local = self._prepare_daily_trigger(
                        trigger_input,
                        invocation.get("temporal_context") or metadata.get("temporal_context"),
                    )
                except ValueError as error:
                    return CapabilityResult(False, False, {"reason": str(error)})
                prepared["trigger"] = prepared_trigger
            prepared.pop("workflow", None)
            result = await super().execute(context, capability_name, prepared)
            if not result.success:
                return result
            updated_job = await self._job(context, task.id)
            if updated_job:
                updated_payload = load_task_payload(updated_job.content)
                updated_payload["version"] = 2
                updated_payload["workflow"] = new_workflow
                if explicit_zone:
                    updated_payload["trigger"]["timezone"] = explicit_zone
                if explicit_local:
                    updated_payload["trigger"]["local_time"] = explicit_local
                updated_job.content = dump_task_payload(updated_payload)
                await context.db.flush()
            return await self._enrich(context, result)

        return await super().execute(context, capability_name, arguments)
