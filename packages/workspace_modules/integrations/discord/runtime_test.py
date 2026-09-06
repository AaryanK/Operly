from __future__ import annotations

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from packages.agent_runtime.inference import OpenAICompatibleAgentModel
from packages.agent_runtime.interactive import Runtime1Agent
from packages.security.execution_context import ExecutionContext


class CapabilityDiscoveryRuntime(Protocol):
    async def available_capabilities(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        query: str,
        limit: int,
    ): ...


async def evaluate_discord_request(
    db: AsyncSession,
    *,
    context: ExecutionContext,
    kernel: CapabilityDiscoveryRuntime,
    prompt: str,
    limit: int = 12,
) -> str:
    """Dry-run one Discord request through Runtime 1.0 without executing capabilities."""

    model = OpenAICompatibleAgentModel()
    agent = Runtime1Agent(model=model)
    objective = await agent.interpreter.interpret(
        message=prompt,
        context=context,
        context_items=(),
    )

    capabilities = ()
    if objective.requires_external_state:
        capabilities = await kernel.available_capabilities(
            db,
            context=context,
            query=objective.capability_query(),
            limit=max(1, min(int(limit), 25)),
        )

    operations = ", ".join(operation.value for operation in objective.operations) or "none"
    resources = ", ".join(objective.resource_hints) or "none"
    lines = [
        "**Operly Runtime dry-run**",
        f"Provider/model: `{model.route.provider}/{model.route.model_id}`",
        f"Scope: `{context.scope_kind.value}` · Surface: `{context.surface.value}`",
        f"Objective: {objective.objective}",
        f"Kind: `{objective.kind.value}` · Dispatch: `{objective.dispatch_path().value}` · Complexity: `{objective.complexity.value}`",
        f"Operations: `{operations}`",
        f"Resources: `{resources}`",
        (
            "Flags: "
            f"external=`{str(objective.requires_external_state).lower()}` · "
            f"mutation=`{str(objective.requires_mutation).lower()}` · "
            f"wait=`{str(objective.requires_future_wait).lower()}`"
        ),
    ]

    if objective.requires_external_state:
        lines.append(f"Capability query: `{objective.capability_query()}`")
        if capabilities:
            lines.append("Authorized candidates:")
            for spec in capabilities:
                approval = " · approval" if spec.approval_required else ""
                lines.append(
                    f"- `{spec.id}` — {spec.display_name} (`{spec.risk.value}`{approval})"
                )
        else:
            lines.append("Authorized candidates: **none**")
    else:
        lines.append("Capability discovery: skipped (model-only request)")

    lines.append("**Nothing was executed or mutated.**")
    return "\n".join(lines)


__all__ = ["evaluate_discord_request"]
