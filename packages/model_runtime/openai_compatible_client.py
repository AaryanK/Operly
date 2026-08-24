from __future__ import annotations

import asyncio
import copy
import json
import os
import random
from typing import Any, Iterable
from uuid import uuid4

import aiohttp

from packages.model_runtime.contracts import ModelInferenceError
from packages.model_runtime.ollama_client import OllamaError
from packages.model_runtime.openrouter_client import _openrouter_messages
from packages.model_runtime.trace_context import (
    ProviderWireEvent,
    current_trace_metadata,
    emit_provider_wire_event,
)


_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_TOOL_CALL_VALIDATION_MARKERS = (
    "tool call validation failed",
    "tool_call_validation",
    "tool_use_failed",
    "failed_generation",
    "parameters for tool",
    "tool arguments",
    "function call validation",
)
_TRACE_RESPONSE_HEADERS = ("x-request-id", "x-reference-id", "x-correlation-id", "retry-after")
_GEMINI_IMPORTED_THOUGHT_SIGNATURE = "skip_thought_signature_validator"


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _first_env(names: Iterable[str]) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _wire_tool_arguments(value: Any) -> str:
    """Serialize provider-neutral parsed tool arguments for OpenAI-compatible wire schemas."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        # Never let an adapter-shape mismatch become a provider 400/agent 500.
        # Stringifying the value keeps the payload valid while preserving a bounded,
        # inspectable representation for providers that require a JSON string field.
        return json.dumps(str(value), ensure_ascii=False)


def _gemini_tool_history(messages: list[dict[str, Any]]) -> None:
    """Make provider-neutral tool history valid for Gemini OpenAI compatibility.

    Gemini 3 returns an opaque thought signature in the first function call of each
    tool-calling step and requires that exact value on replay. Histories produced by
    another model/provider legitimately have no Gemini signature. Google documents a
    validator-skip sentinel for that transfer case, so the Gemini adapter supplies it
    only when the first call is unsigned. Existing signatures are never rewritten.

    This mutates only the adapter-owned deep copy produced by ``_compatible_messages``;
    AgentRuntime and the shared conversation remain provider-neutral.
    """
    for message in messages:
        if str(message.get("role") or "") not in {"assistant", "model"}:
            continue
        calls = message.get("tool_calls")
        if not isinstance(calls, list) or not calls:
            continue
        first_call = next((call for call in calls if isinstance(call, dict)), None)
        if first_call is None:
            continue
        extra = first_call.get("extra_content")
        if not isinstance(extra, dict):
            extra = {}
            first_call["extra_content"] = extra
        google = extra.get("google")
        if not isinstance(google, dict):
            google = {}
            extra["google"] = google
        if not str(google.get("thought_signature") or "").strip():
            google["thought_signature"] = _GEMINI_IMPORTED_THOUGHT_SIGNATURE


def _compatible_messages(
    messages: list[dict[str, Any]],
    *,
    provider: str | None = None,
) -> list[dict[str, Any]]:
    # _openrouter_messages copies the top-level message but intentionally preserves
    # nested provider-neutral structures. Deep-copy here before wire normalization so
    # failover/replay never mutates the caller-owned shared conversation history.
    translated = copy.deepcopy(_openrouter_messages(messages))
    for message in translated:
        message.pop("reasoning_details", None)

        calls = message.get("tool_calls")
        if isinstance(calls, list):
            for call in calls:
                if not isinstance(call, dict):
                    continue
                function = call.get("function")
                if not isinstance(function, dict):
                    continue
                # OpenAI-compatible providers such as Groq require replayed tool
                # calls to declare the canonical discriminator even when the
                # provider that originally produced the call omitted it.
                call["type"] = "function"
                if "arguments" in function:
                    function["arguments"] = _wire_tool_arguments(function.get("arguments"))

    # Some provider-neutral/legacy histories still carry function_call rather
    # than tool_calls. _openrouter_messages currently drops that legacy field,
    # so preserve it explicitly for compatible providers when present.
    for index, original in enumerate(messages):
        if index >= len(translated) or not isinstance(original, dict):
            break
        legacy = original.get("function_call")
        if not isinstance(legacy, dict):
            continue
        normalized = copy.deepcopy(legacy)
        if "arguments" in normalized:
            normalized["arguments"] = _wire_tool_arguments(normalized.get("arguments"))
        translated[index]["function_call"] = normalized

    if str(provider or "").strip().lower() == "gemini":
        _gemini_tool_history(translated)

    return translated


def _provider_generated_tool_error(status: int, detail: str, tools: list[dict[str, Any]]) -> bool:
    """Recognize provider rejection of the model's generated tool call.

    This is not equivalent to a malformed Operly request. The provider accepted
    our messages/schemas, generated a call, then rejected that generated call
    against the schema. Treat it as model-route failure so ModelPool can repair by
    trying another model/provider.
    """
    if status != 400 or not tools:
        return False
    text = str(detail or "").lower()
    return any(marker in text for marker in _TOOL_CALL_VALIDATION_MARKERS)


class OpenAICompatibleClient:
    """Adapter for providers exposing OpenAI-compatible chat completions."""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        default_url: str,
        api_key_envs: tuple[str, ...],
        env_prefix: str,
        fallback_models: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self.provider = str(provider).strip().lower()
        self.env_prefix = str(env_prefix).strip().upper()
        self.url = os.getenv(f"{self.env_prefix}_URL", default_url).strip()
        self.api_key = _first_env(api_key_envs)
        self.model = str(model or "").strip()
        configured = fallback_models or ()
        self.fallback_models = [
            str(item).strip()
            for item in configured
            if str(item).strip() and str(item).strip() != self.model
        ]
        self.fallback_model = self.fallback_models[0] if self.fallback_models else ""
        self.last_model = self.model
        self.last_request_payload: dict[str, Any] | None = None
        self.last_response_payload: Any = None
        self.last_response_status: int | None = None
        self.last_response_metadata: dict[str, str] = {}
        self.timeout_seconds = _bounded_int(
            f"{self.env_prefix}_TIMEOUT_SECONDS", 120, 10, 600
        )
        self.max_attempts = _bounded_int(
            f"{self.env_prefix}_MAX_ATTEMPTS", 2, 1, 5
        )
        self.max_tokens = _bounded_int(
            f"{self.env_prefix}_MAX_TOKENS", 16_384, 256, 65_536
        )

        if not self.provider:
            raise RuntimeError("OpenAI-compatible provider name is missing")
        if not self.url.startswith(("https://", "http://")):
            raise RuntimeError(f"{self.env_prefix}_URL must be an HTTP or HTTPS URL")
        if not self.api_key:
            raise RuntimeError(f"{self.env_prefix}_API_KEY is missing")
        if not self.model:
            raise RuntimeError(f"{self.env_prefix} model is missing")

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
            last_error: OllamaError | None = None
            for index, model in enumerate([self.model, *self.fallback_models]):
                attempts = self.max_attempts if index == 0 else 1
                try:
                    result = await self._chat_model(
                        session,
                        headers,
                        model,
                        messages,
                        tools or [],
                        attempts=attempts,
                    )
                    self.last_model = model
                    return result
                except OllamaError as error:
                    last_error = error
                    if not error.retryable:
                        raise
            raise last_error or OllamaError(
                f"{self.provider} request failed", retryable=True
            )

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
                await asyncio.sleep(
                    min(2 ** (attempt - 1), 8) + random.uniform(0.0, 0.25)
                )
            except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                last_error = OllamaError(
                    f"{self.provider} connection failed", retryable=True
                )
                if attempt >= attempts:
                    raise last_error from error
                await asyncio.sleep(
                    min(2 ** (attempt - 1), 8) + random.uniform(0.0, 0.25)
                )
        raise last_error or OllamaError(
            f"{self.provider} request failed", retryable=True
        )

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
            "messages": _compatible_messages(messages, provider=self.provider),
            "temperature": 0.2,
            "max_tokens": self.max_tokens,
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
                provider=self.provider,
                provider_model_id=model,
                payload=copy.deepcopy(self.last_request_payload),
                metadata=trace_metadata,
            )
        )

        async with session.post(self.url, headers=headers, json=payload) as response:
            response_text = await response.text()
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
            self.last_response_payload = (
                copy.deepcopy(body)
                if isinstance(body, (dict, list))
                else str(response_text or "")[:20_000]
            )
            await emit_provider_wire_event(
                ProviderWireEvent(
                    phase="response",
                    wire_call_id=wire_call_id,
                    provider=self.provider,
                    provider_model_id=model,
                    payload=copy.deepcopy(self.last_response_payload),
                    status=self.last_response_status,
                    response_metadata=dict(self.last_response_metadata),
                    metadata=trace_metadata,
                )
            )

            if response.status != 200:
                upstream = None
                if isinstance(body, dict):
                    error_body = body.get("error")
                    upstream = (
                        error_body.get("message")
                        if isinstance(error_body, dict)
                        else error_body
                    )
                detail = str(upstream or response_text or "unknown upstream error")[:500]
                if _provider_generated_tool_error(response.status, detail, tools):
                    raise ModelInferenceError(
                        f"{self.provider} generated an invalid tool call: {detail}",
                        classification="tool_call_validation",
                        retryable=True,
                        provider=self.provider,
                        model_id=model,
                    )
                # A 413 is normally a route/model capacity or context/TPM limit,
                # not evidence that the provider-neutral request itself is bad.
                if response.status == 413:
                    raise ModelInferenceError(
                        f"{self.provider} request failed (413): {detail}",
                        classification="request_too_large",
                        retryable=False,
                        provider=self.provider,
                        model_id=model,
                    )
                raise OllamaError(
                    f"{self.provider} request failed ({response.status}): {detail}",
                    status=response.status,
                    retryable=response.status in _RETRYABLE_STATUSES,
                )

            if not isinstance(body, dict):
                raise OllamaError(f"{self.provider} returned invalid JSON", retryable=True)
            choices = body.get("choices")
            if not isinstance(choices, list) or not choices:
                raise OllamaError(
                    f"{self.provider} response did not contain choices",
                    retryable=True,
                )
            choice = choices[0] if isinstance(choices[0], dict) else {}
            message = choice.get("message")
            if not isinstance(message, dict):
                raise OllamaError(
                    f"{self.provider} response did not contain a message",
                    retryable=True,
                )

            result = dict(message)
            usage = body.get("usage")
            if isinstance(usage, dict):
                result["usage"] = usage
            finish_reason = choice.get("finish_reason")
            if finish_reason is not None:
                result["finish_reason"] = finish_reason
            return result
