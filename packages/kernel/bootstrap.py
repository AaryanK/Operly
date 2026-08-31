from __future__ import annotations

from packages.kernel.contracts import CapabilityRisk, CapabilitySpec
from packages.kernel.providers import NativeOperlyProvider, ProviderRegistry
from packages.kernel.registry import CapabilityRegistry
from packages.kernel.runtime import OperlyKernelRuntime
from packages.kernel.workspace_control_provider import (
    PROVIDER_ID as WORKSPACE_CONTROL_PROVIDER_ID,
    WorkspaceControlProvider,
    workspace_control_capabilities,
)
from packages.kernel.workspace_os_provider import (
    PROVIDER_ID as WORKSPACE_OS_PROVIDER_ID,
    WorkspaceOSProvider,
    workspace_record_capabilities,
)


def _object(properties: dict, *, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def builtin_capabilities() -> tuple[CapabilitySpec, ...]:
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
            description="Inspect the deterministic Operly kernel state for this authorized scope.",
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
            aliases=("runtime status", "kernel status", "system status"),
            tags=frozenset({"system", "diagnostics"}),
        ),
        CapabilitySpec(
            id="workspace.describe",
            version="1.0.0",
            display_name="Describe workspace",
            description="Return the minimum trusted workspace identity, role, mode, and timezone.",
            provider_id="operly.native",
            scopes=frozenset({"workspace"}),
            input_schema=_object({}),
            output_schema=_object(
                {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "timezone": {"type": "string"},
                    "role": {"type": "string"},
                    "mode": {"type": "string"},
                    "minimum_context": {"type": "object"},
                },
                required=["id", "name", "timezone", "role", "mode", "minimum_context"],
            ),
            permissions=("workspace:read",),
            aliases=("workspace info", "workspace details", "describe workspace"),
            tags=frozenset({"workspace", "identity"}),
        ),
        CapabilitySpec(
            id="workspace.modules.list",
            version="1.0.0",
            display_name="List workspace modules",
            description="List workspace modules visible to the current role and their activation state.",
            provider_id="operly.native",
            scopes=frozenset({"workspace"}),
            input_schema=_object({}),
            output_schema=_object(
                {
                    "modules": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "key": {"type": "string"},
                                "name": {"type": "string"},
                                "category": {"type": "string"},
                                "enabled": {"type": "boolean"},
                                "state": {"type": "string"},
                            },
                            "required": ["key", "name", "category", "enabled", "state"],
                            "additionalProperties": False,
                        },
                    }
                },
                required=["modules"],
            ),
            permissions=("workspace:read",),
            aliases=("list modules", "workspace apps", "workspace capabilities"),
            tags=frozenset({"workspace", "modules"}),
        ),
        CapabilitySpec(
            id="tasks.list",
            version="1.0.0",
            display_name="List tasks",
            description="List tasks only inside the currently authorized personal or workspace scope.",
            provider_id="operly.native",
            scopes=frozenset({"personal", "workspace"}),
            input_schema=_object(
                {
                    "status": {"type": "string", "maxLength": 30},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                }
            ),
            output_schema=_object(
                {"tasks": {"type": "array", "items": task}}, required=["tasks"]
            ),
            permissions=("tasks:read",),
            aliases=("show tasks", "list tasks", "my tasks", "workspace tasks"),
            tags=frozenset({"tasks", "work"}),
        ),
        CapabilitySpec(
            id="tasks.create",
            version="1.0.0",
            display_name="Create task",
            description="Create a task inside the currently authorized personal or workspace scope.",
            provider_id="operly.native",
            scopes=frozenset({"personal", "workspace"}),
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
            tags=frozenset({"tasks", "write"}),
        ),
        CapabilitySpec(
            id="tasks.update_status",
            version="1.0.0",
            display_name="Update task status",
            description="Update status for a task only if it belongs to the authorized scope.",
            provider_id="operly.native",
            scopes=frozenset({"personal", "workspace"}),
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
            tags=frozenset({"tasks", "write"}),
        ),
    )


def build_kernel_runtime() -> OperlyKernelRuntime:
    registry = CapabilityRegistry(
        (
            *builtin_capabilities(),
            *workspace_control_capabilities(),
            *workspace_record_capabilities(),
        )
    )
    providers = ProviderRegistry()
    providers.register("operly.native", NativeOperlyProvider())
    providers.register(WORKSPACE_CONTROL_PROVIDER_ID, WorkspaceControlProvider())
    providers.register(WORKSPACE_OS_PROVIDER_ID, WorkspaceOSProvider())
    return OperlyKernelRuntime(registry=registry, providers=providers)
