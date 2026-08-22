from __future__ import annotations

import asyncio
import json
import os
import random
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import aiohttp


_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_REFERENCE_PATTERN = re.compile(
    r"\bref(?:erence)?(?:\s*id)?\s*[:=]\s*([A-Za-z0-9-]{8,})",
    re.IGNORECASE,
)
_OPENROUTER_DEFAULT_MODEL = "google/gemma-4-31b-it:free"
_OPENROUTER_MODEL_ALIASES = {
    "gemma4:31b": _OPENROUTER_DEFAULT_MODEL,
    "gemma4:31b-cloud": _OPENROUTER_DEFAULT_MODEL,
}


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _safe_error_text(value: object, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _extract_reference(text: str, headers: aiohttp.typedefs.LooseHeaders) -> str | None:
    for header in ("x-request-id", "x-reference-id", "x-correlation-id"):
        value = headers.get(header) if hasattr(headers, "get") else None
        if value:
            return _safe_error_text(value, 120)

    match = _REFERENCE_PATTERN.search(text)
    return match.group(1) if match else None


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None

    try:
        return max(0.0, min(float(value), 60.0))
    except ValueError:
        pass

    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(
            0.0,
            min((parsed - datetime.now(timezone.utc)).total_seconds(), 60.0),
        )
    except (TypeError, ValueError, OverflowError):
        return None


def _openrouter_model(model: str) -> str:
    configured = _first_env("OPEN_ROUTER_MODEL", "OPENROUTER_MODEL")
    if model in _OPENROUTER_MODEL_ALIASES:
        return configured or _OPENROUTER_MODEL_ALIASES[model]
    return model


def _image_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    clean = value.strip()
    if not clean:
        return None
    if clean.startswith(("http://", "https://", "data:")):
        return clean
    return f"data:image/jpeg;base64,{clean}"


def _openrouter_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate the small Ollama chat contract to OpenAI-compatible messages.

    OPERLY stores tool observations with ``tool_name`` because Ollama accepts that
    shape. OpenRouter expects the corresponding ``tool_call_id``. The id is
    application-independent evidence returned by the immediately preceding model
    tool call, so recover it only from that supplied conversation history.
    """

    output: list[dict[str, Any]] = []
    pending_tool_ids: dict[str, list[str]] = {}

    for original in messages:
        message = dict(original)
        role = str(message.get("role") or "user")

        if role == "assistant":
            calls = message.get("tool_calls")
            if isinstance(calls, list):
                for call in calls:
                    if not isinstance(call, dict):
                        continue
                    function = call.get("function") or {}
                    name = str(function.get("name") or "")
                    call_id = str(call.get("id") or "")
                    if name and call_id:
                        pending_tool_ids.setdefault(name, []).append(call_id)

        if role == "tool":
            name = str(message.pop("tool_name", "") or message.get("name") or "")
            if not message.get("tool_call_id") and name:
                ids = pending_tool_ids.get(name) or []
                if ids:
                    message["tool_call_id"] = ids.pop(0)
            message.pop("name", None)

        images = message.pop("images", None)
        if images and role == "user":
            parts: list[dict[str, Any]] = []
            text = message.get("content")
            if text:
                parts.append({"type": "text", "text": str(text)})
            for image in images if isinstance(images, list) else []:
                url = _image_url(image)
                if url:
                    parts.append({"type": "image_url", "image_url": {"url": url}})
            if parts:
                message["content"] = parts

        # Keep only fields accepted by OpenAI-compatible chat messages plus
        # reasoning_details, which OpenRouter asks callers to preserve when a
        # reasoning-capable model returns it.
        allowed = {
            "role",
            "content",
            "name",
            "tool_calls",
            "tool_call_id",
            "reasoning_details",
        }
        output.append({key: value for key, value in message.items() if key in allowed})

    return output


class OllamaError(RuntimeError):
    """A sanitized upstream model-provider failure safe to surface to users.

    The historic class name is retained because it is part of OPERLY's internal
    compatibility API. The client can now speak either native Ollama or OpenRouter.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        reference: str | None = None,
        retryable: bool = False,
    ) -> None:
        self.status = status
        self.reference = reference
        self.retryable = retryable
        super().__init__(_safe_error_text(message))

    @property
    def public_message(self) -> str:
        if self.status in {401, 403}:
            message = "The AI provider rejected OPERLY's credentials."
        elif self.retryable:
            message = "The AI provider is temporarily unavailable. Please retry shortly."
        else:
            message = "The AI provider could not complete this request."

        if self.reference:
            message += f" Reference: {self.reference}."
        return message


class OllamaClient:
    """Shared chat client with a backwards-compatible name.

    If an OpenRouter secret is present, OpenRouter becomes the transport without
    requiring every agent/harness caller to change at once. ``OPEN_ROUTER_API`` is
    accepted because that is the production Railway variable already in use;
    standard OpenRouter key spellings are accepted as aliases as well.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        fallback_models: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        openrouter_key = _first_env(
            "OPEN_ROUTER_API",
            "OPENROUTER_API_KEY",
            "OPEN_ROUTER_API_KEY",
        )
        requested_provider = _first_env("OPERLY_MODEL_PROVIDER").lower()
        self.provider = (
            "openrouter"
            if requested_provider == "openrouter" or (not requested_provider and openrouter_key)
            else "ollama"
        )

        logical_model = (model or os.getenv("OLLAMA_MODEL", "gemma4:31b")).strip()
        configured_fallbacks = (
            fallback_models
            if fallback_models is not None
            else os.getenv(
                "OLLAMA_FALLBACK_MODELS",
                os.getenv("OLLAMA_FALLBACK_MODEL", ""),
            ).split(",")
        )

        if self.provider == "openrouter":
            self.url = _first_env("OPEN_ROUTER_URL", "OPENROUTER_URL") or "https://openrouter.ai/api/v1/chat/completions"
            self.api_key = openrouter_key
            self.model = _openrouter_model(logical_model)
            global_fallbacks = _first_env("OPEN_ROUTER_FALLBACK_MODELS", "OPENROUTER_FALLBACK_MODELS")
            fallback_source = (
                global_fallbacks.split(",")
                if global_fallbacks and fallback_models is None
                else configured_fallbacks
            )
            self.fallback_models = [
                _openrouter_model(str(item).strip())
                for item in fallback_source
                if str(item).strip() and _openrouter_model(str(item).strip()) != self.model
            ]
            self.timeout_seconds = _bounded_int(
                "OPEN_ROUTER_TIMEOUT_SECONDS",
                default=_bounded_int("OLLAMA_TIMEOUT_SECONDS", 180, 15, 600),
                minimum=15,
                maximum=600,
            )
            self.max_attempts = _bounded_int(
                "OPEN_ROUTER_MAX_ATTEMPTS",
                default=_bounded_int("OLLAMA_MAX_ATTEMPTS", 3, 1, 5),
                minimum=1,
                maximum=5,
            )
        else:
            self.url = os.getenv("OLLAMA_URL", "https://ollama.com/api/chat").strip()
            self.api_key = os.getenv("OLLAMA_API_KEY", "").strip()
            self.model = logical_model
            self.fallback_models = [
                str(item).strip()
                for item in configured_fallbacks
                if str(item).strip() and str(item).strip() != self.model
            ]
            self.timeout_seconds = _bounded_int(
                "OLLAMA_TIMEOUT_SECONDS",
                default=180,
                minimum=15,
                maximum=600,
            )
            self.max_attempts = _bounded_int(
                "OLLAMA_MAX_ATTEMPTS",
                default=3,
                minimum=1,
                maximum=5,
            )

        self.fallback_model = self.fallback_models[0] if self.fallback_models else ""
        self.last_model = self.model

        if not self.url.startswith(("https://", "http://")):
            raise RuntimeError("AI provider URL must be an HTTP or HTTPS URL")
        if not self.api_key:
            required = "OPEN_ROUTER_API" if self.provider == "openrouter" else "OLLAMA_API_KEY"
            raise RuntimeError(f"{required} is missing")
        if not self.model:
            raise RuntimeError("AI model is missing")

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(
            total=self.timeout_seconds,
            connect=min(30, self.timeout_seconds),
        )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.provider == "openrouter":
            headers["X-Title"] = "OPERLY"
            public_base = os.getenv("PUBLIC_BASE_URL", "").strip()
            if public_base.startswith(("https://", "http://")):
                headers["HTTP-Referer"] = public_base

        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                result = await self._chat_model(
                    session,
                    headers,
                    self.model,
                    messages,
                    tools or [],
                    attempts=self.max_attempts,
                )
                self.last_model = self.model
                return result
            except OllamaError as primary_error:
                if not self.fallback_models or not primary_error.retryable:
                    raise
                last_error = primary_error
                for fallback_model in self.fallback_models:
                    try:
                        result = await self._chat_model(
                            session,
                            headers,
                            fallback_model,
                            messages,
                            tools or [],
                            attempts=1,
                        )
                        self.last_model = fallback_model
                        return result
                    except OllamaError as fallback_error:
                        last_error = fallback_error
                        if not fallback_error.retryable:
                            break
                raise last_error

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
                return await self._request_once(
                    session,
                    headers,
                    model,
                    messages,
                    tools,
                )
            except OllamaError as error:
                last_error = error
                if not error.retryable or attempt >= attempts:
                    raise
                delay = getattr(error, "retry_after", None)
                await asyncio.sleep(
                    delay if delay is not None else self._backoff_seconds(attempt)
                )
            except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                last_error = OllamaError(
                    "AI provider connection failed",
                    retryable=True,
                )
                if attempt >= attempts:
                    raise last_error from error
                await asyncio.sleep(self._backoff_seconds(attempt))

        raise last_error or OllamaError("AI provider request failed")

    async def _request_once(
        self,
        session: aiohttp.ClientSession,
        headers: dict[str, str],
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if self.provider == "openrouter":
            payload: dict[str, Any] = {
                "model": model,
                "messages": _openrouter_messages(messages),
                "stream": False,
                "temperature": 0.2,
            }
        else:
            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.2},
            }
        if tools:
            payload["tools"] = tools

        async with session.post(
            self.url,
            headers=headers,
            json=payload,
        ) as response:
            response_text = await response.text()
            reference = _extract_reference(response_text, response.headers)

            try:
                body = json.loads(response_text) if response_text else {}
            except json.JSONDecodeError:
                body = None

            if response.status != 200:
                upstream_message: object | None = None
                if isinstance(body, dict):
                    upstream_message = body.get("error") or body.get("message")
                    if isinstance(upstream_message, dict):
                        upstream_message = upstream_message.get("message") or upstream_message.get("code")
                safe_message = _safe_error_text(
                    upstream_message or response_text or "unknown upstream error"
                )
                error = OllamaError(
                    f"AI provider request failed ({response.status}): {safe_message}",
                    status=response.status,
                    reference=reference,
                    retryable=response.status in _RETRYABLE_STATUSES,
                )
                retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
                if retry_after is not None:
                    setattr(error, "retry_after", retry_after)
                raise error

            if not isinstance(body, dict):
                raise OllamaError(
                    "AI provider returned invalid JSON",
                    status=response.status,
                    reference=reference,
                    retryable=True,
                )

            if self.provider == "openrouter":
                choices = body.get("choices")
                message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
            else:
                message = body.get("message")

            if not isinstance(message, dict):
                raise OllamaError(
                    "AI provider response did not contain a message",
                    status=response.status,
                    reference=reference,
                    retryable=True,
                )

            return message

    @staticmethod
    def _backoff_seconds(attempt: int) -> float:
        base = min(2 ** (attempt - 1), 8)
        return base + random.uniform(0.0, 0.35)
