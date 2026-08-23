from __future__ import annotations

import asyncio
import copy
import json
import os
import random
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from uuid import uuid4

import aiohttp

from packages.model_runtime.trace_context import (
    ProviderWireEvent,
    current_trace_metadata,
    emit_provider_wire_event,
)


_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_REFERENCE_PATTERN = re.compile(
    r"\bref(?:erence)?(?:\s*id)?\s*[:=]\s*([A-Za-z0-9-]{8,})",
    re.IGNORECASE,
)
_TRACE_RESPONSE_HEADERS = ("x-request-id", "x-reference-id", "x-correlation-id", "retry-after")


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


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


class OllamaError(RuntimeError):
    """A sanitized upstream provider failure safe to surface to OPERLY users."""

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
    """Native Ollama adapter implementing OPERLY's shared chat contract."""

    def __init__(
        self,
        *,
        model: str | None = None,
        fallback_models: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self.url = os.getenv("OLLAMA_URL", "https://ollama.com/api/chat").strip()
        self.api_key = os.getenv("OLLAMA_API_KEY", "").strip()
        self.model = (model or os.getenv("OLLAMA_MODEL", "gemma4:31b")).strip()
        configured_fallbacks = (
            fallback_models
            if fallback_models is not None
            else os.getenv(
                "OLLAMA_FALLBACK_MODELS",
                os.getenv("OLLAMA_FALLBACK_MODEL", ""),
            ).split(",")
        )
        self.fallback_models = [
            str(item).strip()
            for item in configured_fallbacks
            if str(item).strip() and str(item).strip() != self.model
        ]
        self.fallback_model = self.fallback_models[0] if self.fallback_models else ""
        self.last_model = self.model
        self.last_request_payload: dict[str, Any] | None = None
        self.last_response_payload: Any = None
        self.last_response_status: int | None = None
        self.last_response_metadata: dict[str, str] = {}
        self.timeout_seconds = _bounded_int(
            "OLLAMA_TIMEOUT_SECONDS", default=180, minimum=15, maximum=600
        )
        self.max_attempts = _bounded_int(
            "OLLAMA_MAX_ATTEMPTS", default=3, minimum=1, maximum=5
        )

        if not self.url.startswith(("https://", "http://")):
            raise RuntimeError("OLLAMA_URL must be an HTTP or HTTPS URL")
        if not self.api_key:
            raise RuntimeError("OLLAMA_API_KEY is missing")
        if not self.model:
            raise RuntimeError("OLLAMA_MODEL is missing")

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
                    "Ollama connection failed",
                    retryable=True,
                )
                if attempt >= attempts:
                    raise last_error from error
                await asyncio.sleep(self._backoff_seconds(attempt))

        raise last_error or OllamaError("Ollama request failed")

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
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.2},
        }
        if tools:
            payload["tools"] = tools
        self.last_request_payload = {
            "url": self.url,
            "body": copy.deepcopy(payload),
        }
        self.last_response_payload = None
        self.last_response_status = None
        self.last_response_metadata = {}
        wire_call_id = str(uuid4())
        trace_metadata = current_trace_metadata()
        await emit_provider_wire_event(
            ProviderWireEvent(
                phase="request",
                wire_call_id=wire_call_id,
                provider="ollama",
                provider_model_id=model,
                payload=copy.deepcopy(self.last_request_payload),
                metadata=trace_metadata,
            )
        )

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
            self.last_response_status = int(response.status)
            self.last_response_metadata = {
                key: str(response.headers.get(key))
                for key in _TRACE_RESPONSE_HEADERS
                if response.headers.get(key)
            }
            if reference:
                self.last_response_metadata.setdefault("reference", reference)
            self.last_response_payload = (
                copy.deepcopy(body)
                if isinstance(body, (dict, list))
                else str(response_text or "")[:20_000]
            )
            await emit_provider_wire_event(
                ProviderWireEvent(
                    phase="response",
                    wire_call_id=wire_call_id,
                    provider="ollama",
                    provider_model_id=model,
                    payload=copy.deepcopy(self.last_response_payload),
                    status=self.last_response_status,
                    response_metadata=dict(self.last_response_metadata),
                    metadata=trace_metadata,
                )
            )

            if response.status != 200:
                upstream_message = None
                if isinstance(body, dict):
                    upstream_message = body.get("error") or body.get("message")
                safe_message = _safe_error_text(
                    upstream_message or response_text or "unknown upstream error"
                )
                error = OllamaError(
                    f"Ollama request failed ({response.status}): {safe_message}",
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
                    "Ollama returned invalid JSON",
                    status=response.status,
                    reference=reference,
                    retryable=True,
                )

            message = body.get("message")
            if not isinstance(message, dict):
                raise OllamaError(
                    "Ollama response did not contain a message",
                    status=response.status,
                    reference=reference,
                    retryable=True,
                )

            return message

    @staticmethod
    def _backoff_seconds(attempt: int) -> float:
        base = min(2 ** (attempt - 1), 8)
        return base + random.uniform(0.0, 0.35)
