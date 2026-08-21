from dataclasses import dataclass
from typing import Any

from packages.capabilities.agent_harness import PluginAgentHarness, PluginInvocationContext
from packages.database.db import session_scope
from packages.mcp.policy import active_client_scopes, exposed_tools
from packages.security.principals import PrincipalService


@dataclass(slots=True)
class McpRequestContext:
    tenant_id: str
    user_id: str
    role: str
    client_id: str
    objective: str = "MCP request"
    token_scopes: set[str] | None = None


class McpGateway:
    """Transport-neutral MCP policy gateway.

    MCP is a protocol surface over Operly tools. It never bypasses workspace
    membership, client grants, workspace exposure, or the canonical plugin harness.
    """

    def __init__(self, harness: PluginAgentHarness | None = None) -> None:
        self.harness = harness or PluginAgentHarness()

    async def _allowed(self, context: McpRequestContext):
        invocation = PluginInvocationContext(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            role=context.role,
            objective=context.objective,
            channel="mcp",
            metadata={"client_id": context.client_id},
        )
        authority = await self.harness.authority_for(invocation)
        registry = await self.harness.registry_for(invocation)
        async with session_scope() as db:
            principal = await PrincipalService.user_principal(db, context.user_id)
            exposures = await exposed_tools(
                db,
                tenant_id=context.tenant_id,
                surface="mcp",
                authenticated=True,
            )
            client_scopes = await active_client_scopes(
                db,
                principal_id=principal.id,
                client_id=context.client_id,
                tenant_id=context.tenant_id,
            )
        if context.token_scopes is not None:
            client_scopes = client_scopes.intersection(context.token_scopes)
        allowed = []
        for definition in registry.metadata(context.tenant_id, authority=authority):
            mode = exposures.get(definition.id)
            if not mode:
                continue
            if mode == "public":
                allowed.append(definition)
                continue
            required = set(definition.permissions)
            if "*" in client_scopes or definition.id in client_scopes or required.issubset(client_scopes):
                allowed.append(definition)
        return invocation, allowed

    async def list_tools(self, context: McpRequestContext) -> list[dict[str, Any]]:
        _, allowed = await self._allowed(context)
        return [definition.model_tool_schema()["function"] for definition in allowed]

    async def call_tool(
        self,
        context: McpRequestContext,
        *,
        tool_id: str,
        arguments: dict[str, Any],
        call_id: str | None = None,
    ) -> dict[str, Any]:
        invocation, allowed = await self._allowed(context)
        if tool_id not in {definition.id for definition in allowed}:
            return {"ok": False, "error": "Unknown or unauthorized MCP tool"}
        return await self.harness.invoke(
            tool_id,
            dict(arguments),
            invocation,
            call_id=call_id,
        )
