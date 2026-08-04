import os
from typing import Any

import aiohttp


class OllamaClient:
    def __init__(self) -> None:
        self.url = os.getenv("OLLAMA_URL", "https://ollama.com/api/chat")
        self.api_key = os.getenv("OLLAMA_API_KEY", "")
        self.model = os.getenv("OLLAMA_MODEL", "gemma4:cloud")

        if not self.api_key:
            raise RuntimeError("OLLAMA_API_KEY is missing")

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "options": {
                "temperature": 0.2,
            },
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        timeout = aiohttp.ClientTimeout(total=180)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                self.url,
                headers=headers,
                json=payload,
            ) as response:
                try:
                    body = await response.json()
                except Exception as error:
                    raise RuntimeError(
                        f"Ollama returned invalid JSON ({response.status})"
                    ) from error

                if response.status != 200:
                    message = body.get("error") if isinstance(body, dict) else None
                    raise RuntimeError(
                        f"Ollama request failed ({response.status}): "
                        f"{message or 'unknown error'}"
                    )

                message = body.get("message")
                if not isinstance(message, dict):
                    raise RuntimeError("Ollama response did not contain a message")

                return message
