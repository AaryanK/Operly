from __future__ import annotations

from packages.kernel.contracts import CapabilityRisk, CapabilitySpec
from packages.kernel.providers import NativeOperlyProvider, ProviderRegistry
from packages.kernel.registry import CapabilityRegistry
from packages.kernel.runtime_availability import AvailabilityAwareKernelRuntime


def _object(properties: dict, *, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def builtin_capabilities() -> tuple[CapabilitySpec, ...]:
    """Platform/personal primitives only.

    Workspace capabilities and providers are composed exclusively by
    ``packages.workspace_modules.tools.runtime``. The generic execution substrate has
    no Workspace domain imports or provider registrations.
    """
    task = _object(
        {
            "id": {"type": "string"},
            "title": {"type": "string"},
            "status": {"type": "string"},
            "due_at": {"type": ["string", "null"]},
            "created_at": {"type": ["string", "null"]},
        },
        required=["id", "title", "status", "due_at", "created_at"],
    )
    return (
        CapabilitySpec(
            id="system.runtime.status",
            version="1.0.0",
            display_name="Runtime status",
            description="Inspect the deterministic Operly execution substrate for this authorized scope.",
            provider_id="operly.native",
            scopes=frozenset({"personal", "workspace"}),
            input_schema=_object({}),
            output_schema=_object(
                {
                    "kernel_runtime_enabled": {"type": "boolean"},
                    "ai_runtime_enabled": {"type": "boolean"},
                    "architecture": {"type": "string"},
                    "scope_kind": {"type": "string"},
                    "workspace_mode": {"type": "string"},
                },
                required=[
                    "kernel_runtime_enabled",
                    "ai_runtime_enabled",
                    "architecture",
                    "scope_kind",
                    "workspace_mode",
                ],
            ),
            permissions=("workspace:read",),
            aliases=("runtime status", "system status"),
            tags=frozenset({"system", "diagnostics"}),
        ),
        CapabilitySpec(
            id="tasks.list",
            version="1.0.0",
            display_name="List personal tasks",
            description="List tasks owned by the authenticated Personal scope.",
            provider_id="operly.native",
            scopes=frozenset({"personal"}),
            input_schema=_object(
                {
                    "status": {"type": "string", "maxLength": 30},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                }
            ),
            output_schema=_object({"tasks": {"type": "array", "items": task}}, required=["tasks"]),
            permissions=("tasks:read",),
            aliases=("show tasks", "list tasks", "my tasks"),
            tags=frozenset({"tasks", "personal", "work"}),
        ),
        CapabilitySpec(
            id="tasks.create",
            version="1.0.0",
            display_name="Create personal task",
            description="Create a task owned by the authenticated Personal scope.",
            provider_id="operly.native",
            scopes=frozenset({"personal"}),
            input_schema=_object(
                {
                    "title": {"type": "string", "minLength": 1, "maxLength": 500},
                    "status": {"type": "string", "enum": ["open", "in_progress", "done", "cancelled"]},
                    "due_at": {"type": "string", "maxLength": 80},
                },
                required=["title"],
            ),
            output_schema=_object({"task": task}, required=["task"]),
            permissions=("tasks:write",),
            risk=CapabilityRisk.LOW,
            reversible=True,
            aliases=("create task", "add task", "new task"),
            emits=("task.created",),
            tags=frozenset({"tasks", "personal", "write"}),
        ),
        CapabilitySpec(
            id="tasks.update_status",
            version="1.0.0",
            display_name="Update personal task status",
            description="Update a task only when it belongs to the authenticated Personal scope.",
            provider_id="operly.native",
            scopes=frozenset({"personal"}),
            input_schema=_object(
                {
                    "task_id": {"type": "string", "minLength": 1, "maxLength": 80},
                    "status": {"type": "string", "enum": ["open", "in_progress", "done", "cancelled"]},
                },
                required=["task_id", "status"],
            ),
            output_schema=_object({"task": task}, required=["task"]),
            permissions=("tasks:write",),
            risk=CapabilityRisk.LOW,
            reversible=True,
            aliases=("complete task", "update task", "change task status"),
            emits=("task.updated",),
            tags=frozenset({"tasks", "personal", "write"}),
        ),
    )


def build_kernel_runtime() -> AvailabilityAwareKernelRuntime:
    registry = CapabilityRegistry(builtin_capabilities())
    providers = ProviderRegistry()
    providers.register("operly.native", NativeOperlyProvider())
    return AvailabilityAwareKernelRuntime(registry=registry, providers=providers)
