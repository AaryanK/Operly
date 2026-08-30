from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.models import Task, Tenant
from packages.database.workspace_module_models import WorkspaceModule
from packages.kernel.contracts import CapabilityExecutionResult, CapabilitySpec
from packages.security.execution_context import ExecutionContext
from packages.workspace_modules.catalog import MODULE_CATALOG


class CapabilityProvider(Protocol):
    async def execute(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
        arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        ...


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, CapabilityProvider] = {}

    def register(self, provider_id: str, provider: CapabilityProvider) -> None:
        key = str(provider_id or "").strip().lower()
        if not key:
            raise ValueError("Provider ID is required")
        if key in self._providers:
            raise ValueError(f"Duplicate capability provider: {key}")
        self._providers[key] = provider

    def get(self, provider_id: str) -> CapabilityProvider:
        key = str(provider_id or "").strip().lower()
        try:
            return self._providers[key]
        except KeyError as error:
            raise LookupError(f"Capability provider is unavailable: {key}") from error


Handler = Callable[
    [AsyncSession, ExecutionContext, dict[str, Any], dict[str, Any]],
    Awaitable[CapabilityExecutionResult],
]


class NativeOperlyProvider:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {
            "system.runtime.status": self._runtime_status,
            "workspace.describe": self._workspace_describe,
            "workspace.modules.list": self._workspace_modules,
            "tasks.list": self._tasks_list,
            "tasks.create": self._tasks_create,
            "tasks.update_status": self._tasks_update_status,
        }

    async def execute(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
        arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        handler = self._handlers.get(capability.id)
        if handler is None:
            raise LookupError(f"Native Operly capability is not implemented: {capability.id}")
        return await handler(db, context, arguments, minimum_context)

    async def _runtime_status(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        del db, arguments, minimum_context
        return CapabilityExecutionResult(
            value={
                "kernel_runtime_enabled": True,
                "ai_runtime_enabled": False,
                "architecture": "operly-kernel-v3",
                "scope_kind": context.scope_kind.value,
                "workspace_mode": context.workspace_mode,
            },
            event_payload={"scope_kind": context.scope_kind.value},
        )

    async def _workspace_describe(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        del arguments
        if not context.workspace_id:
            raise PermissionError("Workspace capability requires workspace authority")
        workspace = await db.get(Tenant, context.workspace_id)
        if workspace is None:
            raise LookupError("Workspace is unavailable")
        return CapabilityExecutionResult(
            value={
                "id": workspace.id,
                "name": workspace.name,
                "timezone": workspace.timezone,
                "role": context.role,
                "mode": context.workspace_mode,
                "minimum_context": minimum_context,
            },
            resource_type="workspace",
            resource_id=workspace.id,
        )

    async def _workspace_modules(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        del arguments, minimum_context
        if not context.workspace_id:
            raise PermissionError("Workspace capability requires workspace authority")
        rows = (
            await db.scalars(
                select(WorkspaceModule).where(WorkspaceModule.tenant_id == context.workspace_id)
            )
        ).all()
        persisted = {row.module_key: row for row in rows}
        modules: list[dict[str, Any]] = []
        for key, manifest in MODULE_CATALOG.items():
            required = str(manifest.get("required_permission") or "workspace:read")
            if not context.can(required):
                continue
            row = persisted.get(key)
            enabled = row.enabled if row is not None else bool(manifest.get("default_enabled", False))
            modules.append(
                {
                    "key": key,
                    "name": str(manifest.get("name") or key),
                    "category": str(manifest.get("category") or "other"),
                    "enabled": enabled,
                    "state": row.state if row is not None else ("active" if enabled else "disabled"),
                }
            )
        return CapabilityExecutionResult(
            value={"modules": modules},
            resource_type="workspace",
            resource_id=context.workspace_id,
        )

    def _task_filter(self, context: ExecutionContext):
        if context.is_personal:
            if not context.user_id:
                raise PermissionError("Personal task access requires a user")
            return (
                Task.tenant_id.is_(None),
                Task.owner_user_id == context.user_id,
            )
        if not context.workspace_id:
            raise PermissionError("Workspace task access requires workspace authority")
        return (Task.tenant_id == context.workspace_id,)

    def _task_json(self, task: Task) -> dict[str, Any]:
        return {
            "id": task.id,
            "title": task.title,
            "status": task.status,
            "due_at": task.due_at.isoformat() if task.due_at else None,
            "created_at": task.created_at.isoformat() if task.created_at else None,
        }

    async def _tasks_list(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        del minimum_context
        limit = max(1, min(int(arguments.get("limit") or 50), 100))
        filters = list(self._task_filter(context))
        status = str(arguments.get("status") or "").strip()
        if status:
            filters.append(Task.status == status)
        rows = (
            await db.scalars(
                select(Task)
                .where(*filters)
                .order_by(Task.created_at.desc())
                .limit(limit)
            )
        ).all()
        return CapabilityExecutionResult(value={"tasks": [self._task_json(row) for row in rows]})

    async def _tasks_create(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        del minimum_context
        title = str(arguments["title"]).strip()
        if not title:
            raise ValueError("Task title is required")
        due_at = None
        raw_due = arguments.get("due_at")
        if raw_due:
            try:
                due_at = datetime.fromisoformat(str(raw_due).replace("Z", "+00:00"))
                if due_at.tzinfo is not None:
                    due_at = due_at.astimezone(timezone.utc).replace(tzinfo=None)
            except ValueError as error:
                raise ValueError("due_at must be an ISO-8601 datetime") from error
        task = Task(
            tenant_id=context.workspace_id if context.is_workspace else None,
            owner_user_id=context.user_id if context.is_personal else None,
            title=title,
            status=str(arguments.get("status") or "open"),
            due_at=due_at,
        )
        db.add(task)
        await db.flush()
        return CapabilityExecutionResult(
            value={"task": self._task_json(task)},
            resource_type="task",
            resource_id=task.id,
            event_payload={"task_id": task.id, "status": task.status},
        )

    async def _tasks_update_status(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        del minimum_context
        task_id = str(arguments["task_id"]).strip()
        task = await db.scalar(select(Task).where(Task.id == task_id, *self._task_filter(context)))
        if task is None:
            raise LookupError("Task not found in the authorized scope")
        task.status = str(arguments["status"])
        await db.flush()
        return CapabilityExecutionResult(
            value={"task": self._task_json(task)},
            resource_type="task",
            resource_id=task.id,
            event_payload={"task_id": task.id, "status": task.status},
        )
