from __future__ import annotations

from datetime import datetime

from packages.capabilities.contracts import CapabilityResult
from packages.capabilities.registry_workflow_task_provider import (
    RegistryWorkflowTaskProvider,
    _invoke_capabilities,
)
from packages.capabilities.task_provider import dump_task_payload, load_task_payload
from packages.database.models import ScheduledJob, Task
from packages.plugins import default_plugin_runtime
from packages.tasks.delivery import capture_task_origin, delivery_target_from_origin
from packages.tasks.workflow import WorkflowValidationError, validate_workflow


class UniversalTaskProvider(RegistryWorkflowTaskProvider):
    """Channel-agnostic durable Task compiler.

    The originating surface is captured as data. Execution and delivery are resolved
    later by the generic Task runtime and plugin-contributed delivery adapters.
    Legacy Discord numeric columns remain compatibility metadata only.
    """

    async def _validate_registered_workflow(self, context, workflow: dict | None):
        try:
            spec = validate_workflow(workflow)
        except WorkflowValidationError as error:
            return None, CapabilityResult(False, False, {"reason": str(error)})
        if spec is None or self._personal_scope(context):
            return spec, None
        registry = default_plugin_runtime().manifests
        missing = sorted(
            capability
            for capability in _invoke_capabilities(spec)
            if registry.owner_for_capability(capability) is None
        )
        if missing:
            return None, CapabilityResult(
                False,
                False,
                {
                    "reason": "workflow_capabilities_not_registered",
                    "capabilities": missing,
                    "guidance": "Use capability.search/capability.describe and compile only registered plugin capabilities.",
                },
            )
        return spec, None

    async def _create_universal(self, context, arguments):
        title = " ".join(str(arguments.get("title") or "").split())[:500]
        objective = str(arguments.get("objective") or "").strip()[:12_000]
        if not title or not objective or not context.actor_id:
            return CapabilityResult(False, False, {"reason": "title_objective_actor_required"})

        workflow, error = await self._validate_registered_workflow(
            context,
            arguments.get("workflow"),
        )
        if error is not None:
            return error

        invocation, metadata = self._invocation(context)
        temporal = invocation.get("temporal_context") or metadata.get("temporal_context")
        trigger_input = arguments.get("trigger") if isinstance(arguments.get("trigger"), dict) else {}
        kind = str(trigger_input.get("kind") or "").strip().lower()
        if kind not in {"once", "daily", "interval", "monitor", "event"}:
            return CapabilityResult(False, False, {"reason": "unsupported_trigger_kind"})

        if kind == "event":
            if self._personal_scope(context):
                return CapabilityResult(False, False, {"reason": "personal_event_store_not_supported_yet"})
            if not context.tenant_id:
                return CapabilityResult(False, False, {"reason": "workspace_required_for_event_task"})
            try:
                trigger = self._event_trigger(trigger_input)
            except ValueError as error_value:
                return CapabilityResult(False, False, {"reason": str(error_value)})
            run_at = datetime.utcnow()
            job_status = "waiting_event"
        else:
            prepared_trigger = dict(trigger_input)
            explicit_zone = explicit_local = None
            if kind == "daily":
                try:
                    prepared_trigger, explicit_zone, explicit_local = self._prepare_daily_trigger(
                        trigger_input,
                        temporal,
                    )
                except ValueError as error_value:
                    return CapabilityResult(False, False, {"reason": str(error_value)})
            try:
                trigger, run_at = self._normalized_trigger(
                    {"trigger": prepared_trigger},
                    temporal,
                )
            except (TypeError, ValueError) as error_value:
                return CapabilityResult(False, False, {"reason": str(error_value)})
            if explicit_zone:
                trigger["timezone"] = explicit_zone
            if explicit_local:
                trigger["local_time"] = explicit_local
            job_status = "pending"

        origin = await capture_task_origin(context)
        delivery = str(arguments.get("delivery") or "origin").strip().lower()
        if delivery not in {"origin", "dm", "channel"}:
            return CapabilityResult(False, False, {"reason": "unsupported_delivery"})
        delivery_target = delivery_target_from_origin(origin, delivery)

        tenant_id = None if self._personal_scope(context) else context.tenant_id
        if not tenant_id and not self._personal_scope(context):
            return CapabilityResult(False, False, {"reason": "workspace_required"})

        discord_origin = origin.get("provider") == "discord"
        if discord_origin and (
            not origin.get("external_user_id") or not origin.get("external_conversation_id")
        ):
            return CapabilityResult(False, False, {"reason": "discord_origin_not_resolved"})
        try:
            guild_id = int(origin["external_space_id"]) if discord_origin and origin.get("external_space_id") else None
            channel_id = int(origin["external_conversation_id"]) if discord_origin else 0
            creator_id = int(origin["external_user_id"]) if discord_origin else None
            legacy_user_id = creator_id or 0
        except (TypeError, ValueError):
            return CapabilityResult(False, False, {"reason": "invalid_discord_origin"})

        task = Task(
            tenant_id=tenant_id,
            owner_user_id=context.actor_id,
            guild_id=guild_id,
            channel_id=channel_id if discord_origin else None,
            creator_id=creator_id,
            title=title,
            status="open",
            due_at=None if kind == "event" else run_at,
        )
        context.db.add(task)
        await context.db.flush()

        payload = {
            "version": 3,
            "objective": objective,
            "trigger": trigger,
            "workflow": workflow,
            "delivery": delivery,
            "delivery_target": delivery_target,
            "origin": origin,
            "state": {},
            "event_queue": [],
        }
        job = ScheduledJob(
            tenant_id=tenant_id,
            task_id=task.id,
            guild_id=guild_id,
            channel_id=channel_id,
            user_id=legacy_user_id,
            job_type="task",
            content=dump_task_payload(payload),
            delivery=delivery if discord_origin else "origin",
            run_at=run_at,
            status=job_status,
        )
        context.db.add(job)
        await context.db.flush()
        return CapabilityResult(
            True,
            True,
            {
                "task": {
                    **self._task_view(task, job),
                    "workflow": workflow,
                    "delivery_target": delivery_target,
                }
            },
            task.id,
        )

    async def execute(self, context, capability_name, arguments):
        if capability_name == "task.create":
            return await self._create_universal(context, arguments)

        if capability_name == "task.cancel":
            task = await self._authorized_task(context, str(arguments.get("task_id") or ""))
            if task is not None:
                job = await self._job(context, task.id)
                task.status = "cancelled"
                if job is not None and job.status not in {"completed", "cancelled"}:
                    job.status = "cancelled"
                await context.db.flush()
                return CapabilityResult(
                    True,
                    True,
                    {"task": self._task_view(task, job)},
                    task.id,
                )

        result = await super().execute(context, capability_name, arguments)
        if (
            capability_name == "task.update"
            and result.success
            and arguments.get("delivery") is not None
        ):
            task_id = str(arguments.get("task_id") or "")
            job = await self._job(context, task_id)
            if job is not None:
                payload = load_task_payload(job.content)
                origin = payload.get("origin") if isinstance(payload.get("origin"), dict) else {}
                payload["delivery_target"] = delivery_target_from_origin(
                    origin,
                    str(arguments.get("delivery") or "origin"),
                )
                payload["version"] = max(3, int(payload.get("version") or 1))
                job.content = dump_task_payload(payload)
                await context.db.flush()
        return result
