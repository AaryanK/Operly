import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from packages.business_brain.security import (
    MAX_TOOL_RESULT,
    bounded_text,
    redact_secrets,
)
from packages.business_brain.types import ToolContext
from packages.database.agent_models import AgentToolAudit
from packages.database.db import session_scope

ToolHandler = Callable[[ToolContext, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class RegisteredTool:
    schema: dict[str, Any]
    handler: ToolHandler
    risk: str


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        schema: dict[str, Any],
        handler: ToolHandler,
        *,
        risk: str = "low",
    ) -> None:
        name = schema["function"]["name"]
        if name in self._tools:
            raise ValueError(f"Duplicate tool: {name}")
        self._tools[name] = RegisteredTool(
            schema=schema,
            handler=handler,
            risk=risk,
        )

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema for tool in self._tools.values()]

    async def execute(
        self,
        name: str,
        context: ToolContext,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        registered = self._tools.get(name)

        if registered is None:
            result = {"ok": False, "error": "Unknown or unauthorized tool"}
            await self._audit(name, "blocked", context, arguments, result)
            return result

        safe_arguments = redact_secrets(arguments)

        try:
            result = await registered.handler(context, arguments)
            if not isinstance(result, dict):
                result = {"ok": False, "error": "Tool returned an invalid result"}
        except Exception as error:
            result = {
                "ok": False,
                "error": bounded_text(str(error), 600),
            }

        serialized = json.dumps(
            redact_secrets(result),
            ensure_ascii=False,
            default=str,
        )
        if len(serialized) > MAX_TOOL_RESULT:
            result = {
                "ok": bool(result.get("ok")),
                "summary": serialized[:MAX_TOOL_RESULT] + "[truncated]",
            }

        await self._audit(
            name,
            registered.risk,
            context,
            safe_arguments,
            result,
        )
        return result

    async def _audit(
        self,
        name: str,
        risk: str,
        context: ToolContext,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        async with session_scope() as db:
            db.add(
                AgentToolAudit(
                    tenant_id=context.tenant_id,
                    principal_id=context.principal_id,
                    conversation_id=context.conversation_id,
                    channel=context.channel,
                    tool_name=name,
                    risk=risk,
                    arguments_json=json.dumps(
                        redact_secrets(arguments),
                        ensure_ascii=False,
                        default=str,
                    ),
                    result_json=json.dumps(
                        redact_secrets(result),
                        ensure_ascii=False,
                        default=str,
                    ),
                    success=bool(result.get("ok")),
                )
            )
