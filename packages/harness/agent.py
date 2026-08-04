import json
from typing import Any

import aiohttp

from packages.harness.context import ToolContext
from packages.harness.registry import ToolRegistry


class AgentHarness:
    def __init__(
        self,
        *,
        api_url: str,
        api_key: str,
        model: str,
        registry: ToolRegistry,
        max_steps: int = 5,
    ) -> None:
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.registry = registry
        self.max_steps = max_steps

    async def _chat(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": self.registry.schemas(),
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=180),
            ) as response:
                data = await response.json()
                if response.status != 200:
                    raise RuntimeError(data)
                return data["message"]

    async def run(
        self,
        *,
        context: ToolContext,
        system_prompt: str,
        user_content: str,
    ) -> str:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        for _ in range(self.max_steps):
            assistant_message = await self._chat(messages)
            messages.append(assistant_message)

            tool_calls = assistant_message.get("tool_calls") or []
            if not tool_calls:
                return (assistant_message.get("content") or "").strip()

            for call in tool_calls:
                function = call.get("function", {})
                name = function.get("name", "")
                arguments = function.get("arguments") or {}

                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}

                result = await self.registry.execute(name, context, arguments)

                messages.append(
                    {
                        "role": "tool",
                        "tool_name": name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        return "I could not complete that safely within the tool limit."
