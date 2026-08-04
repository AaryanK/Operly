from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from packages.harness.context import ToolContext

ToolHandler = Callable[[ToolContext, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class RegisteredTool:
    schema: dict[str, Any]
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, schema: dict[str, Any], handler: ToolHandler) -> None:
        name = schema["function"]["name"]
        if name in self._tools:
            raise ValueError(f"Duplicate tool: {name}")
        self._tools[name] = RegisteredTool(schema=schema, handler=handler)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema for tool in self._tools.values()]

    async def execute(
        self,
        name: str,
        context: ToolContext,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            return {"ok": False, "error": f"Unknown tool: {name}"}
        return await tool.handler(context, arguments)
