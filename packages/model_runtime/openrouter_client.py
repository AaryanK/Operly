from __future__ import annotations

import asyncio
import json
import os
import random
from typing import Any

import aiohttp

from packages.model_runtime.ollama_client import OllamaError


_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _api_key() -> str:
    for name in ("OPEN_ROUTER_API", "OPENROUTER_API_KEY", "OPEN_ROUTER_API_KEY"):
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _tool_result_message(message: dict[str, Any]) -> dict[str, Any]:
    converted = dict(message)
    if converted.get("role") == "tool":
        tool_call_id = converted.pop("tool_call_id", None) or converted.pop("tool_name", None)
        if tool_call_id:
            converted["tool_call_id"] = str(tool_call_id)
    return converted


def _convert_content(message: dict[str, Any]) -> dict[str, Any]:
    converted = _tool_result_message(message)
    images = converted.pop("images", None)
    if not images:
        return converted

    content: list[dict[str, Any]] = []
    text = converted.get("content")
    if text:
        content.append({"type": "text", "text": str(text)})
    for image in list(images)[:4]:
        value = str(image or "").strip()
        if not value:
            continue
        url = value if value.startswith(("http://", "https://", "data:")) else f"data:image/png;base64,{value}"
        content.append({"type": "image_url", "image_url": {"url": url}})
    converted["content"] = content
    return converted


class OpenRouterClient:
    """OpenRouter adapter implementing OPERLY's provider-neutral chat contract."""

    def __init__(
        self,
        *,
        model: str | None = None,
        fallback_models: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self.url = os.getenv(
            "OPEN_ROUTER_URL", "https://openrouter.ai/api/v1/chat/completions"
        ).strip()
        self.api_key = _api_key()
        self.model = (model or os.getenv("OPEN_ROUTER_MODEL", "openai/gpt-oss-120b:free")).strip()
        configured = fallback_models if fallback_models is not None else os.getenv("OPEN_ROUTER_FALLBACK_MODELS", "").split(",")
        self.fallback_models = [
            str(item).strip()
            for item in configured
            if str(item).strip() and str(item).strip() != self.model
        ]
        self.fallback_model = self.fallback_models[0] if self.fallback_models else ""
        self.last_model = self.model
        self.timeout_seconds = _bounded_int("OPEN_ROUTER_TIMEOUT_SECONDS", 180, 15, 600)
        self.max_attempts = _bounded_int("OPEN_ROUTER_MAX_ATTEMPTS", 3, 1, 5)

        if not self.url.startswith(("https://", "http://")):
            raise RuntimeError("OPEN_ROUTER_URL must be an HTTP or HTTPS URL")
        if not self.api_key:
            raise RuntimeError("OPEN_ROUTER_API is missing")
        if not self.model:
            raise RuntimeError("OPEN_ROUTER_MODEL is missing")

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds, connect=min(30, self.timeout_seconds))
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "HTTP-Referer": os.getenv("PUBLIC_BASE_URL", "https://operly.dragonzpyder.xyz"),
            "X-Title": "OPERLY",
        }
        async with aiohttp.ClientSession(timeout=timeout) as session:
            models = [self.model, *self.fallback_models]
            last_error: OllamaError | None = None
            for index, model in enumerate(models):
                attempts = self.max_attempts if index == 0 else 1
                try:
                    result = await self._chat_model(session, headers, model, messages, tools or [], attempts=attempts)
                    self.last_model = model
                    return result
                except OllamaError as error:
                    last_error = error
                    if not error.retryable:
                        raise
            raise last_error or OllamaError("OpenRouter request failed")

    async def _chat_model(
        self,
        session: aiohttp.ClientSession,
        headers: dict[str, str],
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        attempts: int,
    ) -> dict[str, Any]:
        last_error: OllamaError | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await self._request_once(session, headers, model, messages, tools)
            except OllamaError as error:
                last_error = error
                if not error.retryable or attempt >= attempts:
                    raise
                await asyncio.sleep(min(2 ** (attempt - 1), 8) + random.uniform(0.0, 0.35))
            except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                last_error = OllamaError("OpenRouter connection failed", retryable=True)
                if attempt >= attempts:
                    raise last_error from error
                await asyncio.sleep(min(2 ** (attempt - 1), 8) + random.uniform(0.0, 0.35))
        raise last_error or OllamaError("OpenRouter request failed")

    async def _request_once(
        self,
        session: aiohttp.ClientSession,
        headers: dict[str, str],
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [_convert_content(message) for message in messages],
            "temperature": 0.2,
        }
        if tools:
            payload["tools"] = tools

        async with session.post(self.url, headers=headers, json=payload) as response:
            response_text = await response.text()
            try:
                body = json.loads(response_text) if response_text else {}
            except json.JSONDecodeError:
                body = None

            if response.status != 200:
                upstream = None
                if isinstance(body, dict):
                    error_body = body.get("error")
                    upstream = error_body.get("message") if isinstance(error_body, dict) else error_body
                raise OllamaError(
                    f"OpenRouter request failed ({response.status}): {str(upstream or response_text or 'unknown upstream error')[:500]}",
                    status=response.status,
                    retryable=response.status in _RETRYABLE_STATUSES,
                )

            if not isinstance(body, dict):
                raise OllamaError("OpenRouter returned invalid JSON", retryable=True)
            choices = body.get("choices")
            if not isinstance(choices, list) or not choices:
                raise OllamaError("OpenRouter response did not contain choices", retryable=True)
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if not isinstance(message, dict):
                raise OllamaError("OpenRouter response did not contain a message", retryable=True)
            return message
