from typing import Any

from packages.business_brain.operations_service import get_operations_service
from packages.business_brain.registry import ToolRegistry
from packages.business_brain.types import ToolContext


def schema(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


async def get_operations_brief(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    return await get_operations_service().operational_brief(
        context.tenant_id,
        context.principal_id,
    )


async def run_operational_scan(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    alerts = await get_operations_service().run_operational_scan(
        context.tenant_id,
        context.actor_name,
    )
    return {
        "ok": True,
        "active_alerts": alerts,
    }


async def run_business_audit(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    result = await get_operations_service().run_audit(
        context.tenant_id,
        context.principal_id,
    )
    return {"ok": True, "audit": result}


async def generate_operating_plan(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    goal = str(args.get("goal", "")).strip()
    result = await get_operations_service().generate_plan(
        context.tenant_id,
        context.principal_id,
        goal,
    )
    return {"ok": True, "plan": result}


def register_operations_tools(registry: ToolRegistry) -> None:
    registry.register(
        schema(
            "get_operations_brief",
            "Read the owner's prioritized operational brief.",
            {},
        ),
        get_operations_brief,
    )
    registry.register(
        schema(
            "run_operational_scan",
            "Scan tenant-scoped business data for operational exceptions.",
            {},
        ),
        run_operational_scan,
        risk="medium",
    )
    registry.register(
        schema(
            "run_business_audit",
            "Create a new internal business health audit.",
            {},
        ),
        run_business_audit,
        risk="medium",
    )
    registry.register(
        schema(
            "generate_operating_plan",
            "Generate a draft visual operating plan from a business goal.",
            {
                "goal": {
                    "type": "string",
                    "description": "The process or business outcome to design.",
                }
            },
            ["goal"],
        ),
        generate_operating_plan,
        risk="medium",
    )
