"""Model objects, selection, pools, and cross-provider failover.

This is the orchestration boundary above provider adapters. Callers ask for a
Model or ModelSelector and invoke ``infer``; only this package constructs provider
clients or interprets provider failures.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable, Iterable
from uuid import uuid4

from packages.model_runtime.catalog import ModelResource, model_resources
from packages.model_runtime.contracts import (
    InferenceBudget,
    InferenceRequest,
    InferenceResult,
    Model,
    ModelInferenceError,
    ModelSelector,
    ModelTraits,
    ModelUsage,
)
from packages.model_runtime.ollama_client import OllamaError
from packages.model_runtime.portfolio import ModelRoute, model_route
from packages.model_runtime.providers import model_client_for_route
from packages.model_runtime.routing_policy import (
    auto_portfolio_enabled,
    role_routing_profile,
)
from packages.model_runtime.trace_context import current_trace_metadata


@dataclass(frozen=True, slots=True)
class ModelAttemptEvent:
    phase: str
    resource_id: str
    provider: str
    provider_model_id: str
    attempt: int
    latency_ms: int | None = None
    classification: str | None = None
    retryable: bool | None = None
    detail: str | None = None
    attempt_id: str | None = None
    metadata: dict[str, Any] | None = None
    input_payload: dict[str, Any] | None = None
    output_payload: dict[str, Any] | None = None


TelemetrySink = Callable[[ModelAttemptEvent], Awaitable[None] | None]
_TELEMETRY_SINKS: list[TelemetrySink] = []


def register_model_telemetry_sink(sink: TelemetrySink) -> None:
    if sink not in _TELEMETRY_SINKS:
        _TELEMETRY_SINKS.append(sink)


async def _emit(event: ModelAttemptEvent) -> None:
    for sink in tuple(_TELEMETRY_SINKS):
        try:
            result = sink(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            continue


def _failure(error: BaseException, *, provider: str, model_id: str) -> ModelInferenceError:
    if isinstance(error, ModelInferenceError):
        return error
    if isinstance(error, asyncio.TimeoutError):
        return ModelInferenceError(
            "Model inference timed out",
            classification="response_timeout",
            retryable=True,
            provider=provider,
            model_id=model_id,
        )
    if isinstance(error, OllamaError):
        status = getattr(error, "status", None)
        classification = (
            "quota_or_credits"
            if status == 402
            else "rate_limited"
            if status == 429
            else "auth"
            if status in {401, 403}
            else "model_unavailable"
            if status == 404
            else "provider_5xx"
            if status and status >= 500
            else "invalid_request"
            if status and 400 <= status < 500
            else "provider_error"
        )
        return ModelInferenceError(
            str(error),
            classification=classification,
            retryable=bool(getattr(error, "retryable", False)),
            provider=provider,
            model_id=model_id,
        )
    return ModelInferenceError(
        str(error) or type(error).__name__,
        classification="model_error",
        retryable=False,
        provider=provider,
        model_id=model_id,
    )


def _usage(message: dict[str, Any]) -> ModelUsage | None:
    raw = message.get("usage")
    if not isinstance(raw, dict):
        return None
    return ModelUsage(
        input_tokens=raw.get("prompt_tokens") or raw.get("input_tokens"),
        output_tokens=raw.get("completion_tokens") or raw.get("output_tokens"),
        total_tokens=raw.get("total_tokens"),
    )


class ConfiguredModel:
    """One configured model resource backed by one provider adapter."""

    def __init__(
        self,
        *,
        resource_id: str,
        provider: str,
        provider_model_id: str,
        tags: Iterable[str] = (),
        capabilities: Iterable[str] = ("text",),
        traits: ModelTraits | None = None,
        priority: int = 100,
        verified_latency_ms: int | None = None,
        canonical_id: str | None = None,
    ) -> None:
        self.id = str(resource_id).strip()
        self.provider = str(provider).strip().lower()
        self.provider_model_id = str(provider_model_id).strip()
        self.tags = frozenset(str(x).strip().lower() for x in tags if str(x).strip())
        self.capabilities = frozenset(
            str(x).strip().lower() for x in capabilities if str(x).strip()
        )
        self.traits = traits or ModelTraits()
        self.priority = int(priority)
        self.verified_latency_ms = (
            int(verified_latency_ms) if verified_latency_ms is not None else None
        )
        self.canonical_id = str(canonical_id or self.provider_model_id).strip()
        if not self.id or not self.provider or not self.provider_model_id:
            raise ValueError("Configured model requires resource, provider, and model ids")

    def _client(self, *, budget: InferenceBudget | None = None):
        client = model_client_for_route(
            ModelRoute(provider=self.provider, primary=self.provider_model_id)
        )
        if hasattr(client, "max_attempts"):
            client.max_attempts = 1
        if hasattr(client, "fallback_models"):
            client.fallback_models = []
        if hasattr(client, "fallback_model"):
            client.fallback_model = ""
        if (
            budget
            and budget.max_output_tokens is not None
            and hasattr(client, "max_tokens")
        ):
            client.max_tokens = max(1, int(budget.max_output_tokens))
        return client

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        budget = request.budget or InferenceBudget()
        attempts = max(1, min(int(budget.attempts_per_model or 1), 5))
        last_error: ModelInferenceError | None = None
        trace_metadata = current_trace_metadata()
        trace_metadata.update(dict(request.metadata))
        request_payload = {
            "messages": [dict(item) for item in request.messages],
            "tools": [dict(item) for item in request.tools],
            "responseSchema": request.response_schema,
            "modalityInputs": [dict(item) for item in request.modality_inputs],
            "budget": asdict(budget),
        }

        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            attempt_id = str(uuid4())
            await _emit(
                ModelAttemptEvent(
                    phase="start",
                    resource_id=self.id,
                    provider=self.provider,
                    provider_model_id=self.provider_model_id,
                    attempt=attempt,
                    attempt_id=attempt_id,
                    metadata=trace_metadata,
                    input_payload=request_payload,
                )
            )
            try:
                client = self._client(budget=budget)
                call = client.chat(list(request.messages), list(request.tools))
                if budget.timeout_seconds is not None:
                    message = await asyncio.wait_for(
                        call,
                        timeout=max(1.0, float(budget.timeout_seconds)),
                    )
                else:
                    message = await call
                if not isinstance(message, dict):
                    raise ModelInferenceError(
                        "Model returned a malformed message",
                        classification="malformed_response",
                        retryable=True,
                        provider=self.provider,
                        model_id=self.provider_model_id,
                    )
                latency = int((time.monotonic() - started) * 1000)
                actual_model = str(
                    getattr(client, "last_model", None) or self.provider_model_id
                )
                usage = _usage(message)
                await _emit(
                    ModelAttemptEvent(
                        phase="success",
                        resource_id=self.id,
                        provider=self.provider,
                        provider_model_id=actual_model,
                        attempt=attempt,
                        latency_ms=latency,
                        attempt_id=attempt_id,
                        metadata=trace_metadata,
                        output_payload={
                            "message": dict(message),
                            "finishReason": message.get("finish_reason"),
                            "usage": asdict(usage) if usage is not None else None,
                        },
                    )
                )
                return InferenceResult(
                    message=message,
                    model_resource_id=self.id,
                    provider=self.provider,
                    provider_model_id=actual_model,
                    latency_ms=latency,
                    usage=usage,
                    finish_reason=message.get("finish_reason"),
                    attempt=attempt,
                )
            except BaseException as raw:
                error = _failure(
                    raw,
                    provider=self.provider,
                    model_id=self.provider_model_id,
                )
                last_error = error
                latency = int((time.monotonic() - started) * 1000)
                await _emit(
                    ModelAttemptEvent(
                        phase="error",
                        resource_id=self.id,
                        provider=self.provider,
                        provider_model_id=self.provider_model_id,
                        attempt=attempt,
                        latency_ms=latency,
                        classification=error.classification,
                        retryable=error.retryable,
                        detail=str(error)[:2000],
                        attempt_id=attempt_id,
                        metadata=trace_metadata,
                        output_payload={
                            "errorType": type(raw).__name__,
                            "message": str(error),
                            "classification": error.classification,
                            "retryable": error.retryable,
                        },
                    )
                )
                if not error.retryable or attempt >= attempts:
                    raise error from raw

        raise last_error or ModelInferenceError("Model inference failed")


def _cooldown_seconds() -> float:
    try:
        value = float(os.getenv("OPERLY_MODEL_POOL_COOLDOWN_SECONDS", "45"))
    except ValueError:
        value = 45.0
    return max(5.0, min(value, 600.0))


class ModelPool:
    """Ordered model candidates with sticky success and circuit-breaker failover."""

    _PROVIDER_WIDE_FAILURES = {
        "rate_limited",
        "quota_or_credits",
        "provider_5xx",
        "provider_error",
        "auth",
    }

    def __init__(self, models: Iterable[Model], *, id: str = "model-pool") -> None:
        self.models = tuple(models)
        if not self.models:
            raise ValueError("ModelPool requires at least one model")
        self.id = id
        self.tags = frozenset().union(*(model.tags for model in self.models))
        self.capabilities = frozenset.intersection(
            *(frozenset(model.capabilities) for model in self.models)
        )
        self.traits = self.models[0].traits
        self._preferred_model_id: str | None = None
        self._model_cooldown_until: dict[str, float] = {}
        self._provider_cooldown_until: dict[str, float] = {}

    def _ordered_candidates(self) -> list[Model]:
        now = time.monotonic()

        def available(model: Model) -> bool:
            provider = str(getattr(model, "provider", "") or "")
            return (
                self._model_cooldown_until.get(model.id, 0.0) <= now
                and self._provider_cooldown_until.get(provider, 0.0) <= now
            )

        healthy = [model for model in self.models if available(model)]
        cooling = [model for model in self.models if not available(model)]
        if self._preferred_model_id:
            healthy.sort(
                key=lambda model: 0 if model.id == self._preferred_model_id else 1
            )
        return healthy + cooling

    def _mark_failure(self, model: Model, error: ModelInferenceError) -> None:
        until = time.monotonic() + _cooldown_seconds()
        self._model_cooldown_until[model.id] = until
        provider = str(
            error.provider or getattr(model, "provider", "") or ""
        ).strip().lower()
        if provider and error.classification in self._PROVIDER_WIDE_FAILURES:
            self._provider_cooldown_until[provider] = until
        if self._preferred_model_id == model.id:
            self._preferred_model_id = None

    def _mark_success(self, model: Model) -> None:
        self._preferred_model_id = model.id
        self._model_cooldown_until.pop(model.id, None)
        provider = str(getattr(model, "provider", "") or "").strip().lower()
        if provider:
            self._provider_cooldown_until.pop(provider, None)

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        budget = request.budget or InferenceBudget()
        candidates = self._ordered_candidates()
        if budget.max_models is not None:
            candidates = candidates[: max(1, int(budget.max_models))]
        last_error: ModelInferenceError | None = None
        for model in candidates:
            provider = str(getattr(model, "provider", "") or "").strip().lower()
            now = time.monotonic()
            if self._model_cooldown_until.get(model.id, 0.0) > now:
                continue
            if provider and self._provider_cooldown_until.get(provider, 0.0) > now:
                continue
            try:
                result = await model.infer(request)
                self._mark_success(model)
                return result
            except ModelInferenceError as error:
                last_error = error
                self._mark_failure(model, error)
                # A 4xx/auth failure can be specific to one model or provider. The
                # portfolio is the recovery boundary, so continue to another eligible
                # candidate instead of requiring the user to repeat the chat turn.
                continue
        raise last_error or ModelInferenceError("All configured models failed")


class ModelRegistry:
    def __init__(self) -> None:
        self._models: dict[str, ConfiguredModel] = {}
        self._catalog_ids: set[str] = set()

    def configure(
        self,
        *,
        id: str,
        provider: str,
        model: str,
        credential_ref: str | None = None,
        tags: Iterable[str] = (),
        capabilities: Iterable[str] = ("text",),
        traits: ModelTraits | None = None,
        priority: int = 100,
        verified_latency_ms: int | None = None,
        canonical_id: str | None = None,
        replace: bool = False,
    ) -> ConfiguredModel:
        del credential_ref
        configured = ConfiguredModel(
            resource_id=id,
            provider=provider,
            provider_model_id=model,
            tags=tags,
            capabilities=capabilities,
            traits=traits,
            priority=priority,
            verified_latency_ms=verified_latency_ms,
            canonical_id=canonical_id,
        )
        if configured.id in self._models and not replace:
            raise ValueError(f"Model already configured: {configured.id}")
        self._models[configured.id] = configured
        return configured

    def register_resource(
        self, resource: ModelResource, *, replace: bool = True
    ) -> ConfiguredModel:
        tags = set(getattr(resource, "tags", frozenset()))
        if resource.free:
            tags.add("free")
        traits = ModelTraits(
            context_tokens=resource.context_length,
            latency_class=resource.latency_class,
            cost_class=resource.cost_class or ("free" if resource.free else None),
            quality_class=resource.quality_class,
            locality=resource.locality,
        )
        return self.configure(
            id=f"{resource.provider}:{resource.id}",
            provider=resource.provider,
            model=resource.id,
            tags=tags,
            capabilities=resource.capabilities,
            traits=traits,
            priority=resource.priority,
            verified_latency_ms=resource.verified_latency_ms,
            canonical_id=resource.canonical_id or resource.id,
            replace=replace,
        )

    def refresh_catalog(self) -> None:
        for resource_id in self._catalog_ids:
            self._models.pop(resource_id, None)
        self._catalog_ids.clear()
        for resource in model_resources():
            configured = self.register_resource(resource, replace=True)
            self._catalog_ids.add(configured.id)

    def get(self, resource_id: str) -> ConfiguredModel:
        try:
            return self._models[resource_id]
        except KeyError as error:
            raise LookupError(f"Unknown model resource: {resource_id}") from error

    def candidates(self, selector: ModelSelector) -> tuple[ConfiguredModel, ...]:
        self.refresh_catalog()
        required = set(selector.requires)
        avoid = set(selector.avoid_tags)
        excluded = set(selector.exclude_resource_ids)
        rows = [
            model
            for model in self._models.values()
            if model.id not in excluded
            and required.issubset(model.capabilities)
            and not (avoid & model.tags)
        ]
        preferred = set(selector.prefer_tags)
        rows.sort(
            key=lambda model: (
                -len(preferred & model.tags),
                0 if (selector.prefer_free and "free" in model.tags) else 1,
                model.priority,
                model.verified_latency_ms or 10**9,
                model.id,
            )
        )
        return tuple(rows)

    def resolve(self, selector: ModelSelector) -> ConfiguredModel:
        candidates = self.candidates(selector)
        if not candidates:
            raise LookupError(
                "No model satisfies required capabilities: "
                + ", ".join(sorted(selector.requires))
            )
        return candidates[0]

    def pool(self, selector: ModelSelector, *, limit: int | None = None) -> ModelPool:
        candidates = self.candidates(selector)
        if limit is not None:
            candidates = candidates[: max(1, int(limit))]
        return ModelPool(candidates, id="selector-pool")


_DEFAULT_REGISTRY: ModelRegistry | None = None


def default_model_registry() -> ModelRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = ModelRegistry()
    _DEFAULT_REGISTRY.refresh_catalog()
    return _DEFAULT_REGISTRY


def _role_env_key(role: str) -> str:
    return "OPERLY_MODEL_" + "".join(
        ch if ch.isalnum() else "_" for ch in str(role).upper()
    )


def _configured_role_candidates(role: str) -> list[dict[str, Any]]:
    raw = os.getenv(_role_env_key(role) + "_CANDIDATES_JSON", "").strip()
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"{_role_env_key(role)}_CANDIDATES_JSON must be valid JSON"
        ) from error
    if not isinstance(value, list):
        raise RuntimeError(f"{_role_env_key(role)}_CANDIDATES_JSON must be an array")
    return [item for item in value if isinstance(item, dict)]


def _provider_diverse(
    candidates: Iterable[ConfiguredModel],
    *,
    limit: int,
) -> list[ConfiguredModel]:
    rows = list(candidates)
    selected: list[ConfiguredModel] = []
    seen_providers: set[str] = set()

    for model in rows:
        if model.provider in seen_providers:
            continue
        selected.append(model)
        seen_providers.add(model.provider)
        if len(selected) >= limit:
            return selected

    for model in rows:
        if model in selected:
            continue
        selected.append(model)
        if len(selected) >= limit:
            break
    return selected


def _legacy_role_models(
    registry: ModelRegistry,
    role: str,
) -> list[ConfiguredModel]:
    route = model_route(role)
    resources = model_resources()
    models: list[ConfiguredModel] = []
    for index, model_id in enumerate((route.primary, *route.fallbacks)):
        resource_id = f"role:{role}:{index}:{route.provider}:{model_id}"
        resource = next(
            (
                item
                for item in resources
                if item.provider == route.provider and item.id == model_id
            ),
            None,
        )
        capabilities = resource.capabilities if resource else frozenset(
            {"text", "tools", "reasoning", "coding"}
        )
        tags = set(getattr(resource, "tags", frozenset())) if resource else set()
        if resource and resource.free:
            tags.add("free")
        models.append(
            registry.configure(
                id=resource_id,
                provider=route.provider,
                model=model_id,
                tags=tags,
                capabilities=capabilities,
                traits=ModelTraits(
                    context_tokens=resource.context_length if resource else None,
                    latency_class=resource.latency_class if resource else None,
                    cost_class=(resource.cost_class if resource else None)
                    or ("free" if resource and resource.free else None),
                    quality_class=resource.quality_class if resource else None,
                    locality=resource.locality if resource else None,
                ),
                priority=resource.priority if resource else 100,
                verified_latency_ms=(
                    resource.verified_latency_ms if resource else None
                ),
                canonical_id=(resource.canonical_id if resource else model_id),
                replace=True,
            )
        )
    return models


def model_for_role(role: str) -> Model:
    """Resolve a role into an explicit chain or an automatic provider-diverse pool."""
    registry = default_model_registry()
    configured = _configured_role_candidates(role)
    models: list[ConfiguredModel] = []

    if configured:
        for index, item in enumerate(configured):
            provider = str(item.get("provider") or "").strip().lower()
            model_id = str(item.get("model") or item.get("id") or "").strip()
            if not provider or not model_id:
                continue
            tags = item.get("tags") or []
            capabilities = item.get("capabilities") or [
                "text",
                "tools",
                "reasoning",
                "coding",
            ]
            resource_id = str(
                item.get("resource_id")
                or f"role:{role}:{index}:{provider}:{model_id}"
            )
            models.append(
                registry.configure(
                    id=resource_id,
                    provider=provider,
                    model=model_id,
                    tags=tags,
                    capabilities=capabilities,
                    priority=int(item.get("priority", 100)),
                    canonical_id=str(item.get("canonical_id") or model_id),
                    replace=True,
                )
            )
    elif auto_portfolio_enabled():
        profile = role_routing_profile(role)
        candidates = [
            model
            for model in registry.candidates(profile.selector())
            if not model.id.startswith("role:")
        ]
        if candidates:
            models = _provider_diverse(
                candidates,
                limit=max(1, profile.max_models),
            )

    if not models:
        models = _legacy_role_models(registry, role)

    if not models:
        raise LookupError(f"No model configured for role: {role}")
    return models[0] if len(models) == 1 else ModelPool(models, id=f"role:{role}")


class ModelChatAdapter:
    """Temporary adapter for legacy loops that still call ``chat``."""

    def __init__(self, model: Model, *, budget: InferenceBudget | None = None) -> None:
        self.model = model
        self.budget = budget
        self.last_result: InferenceResult | None = None

    @property
    def last_model(self) -> str:
        return self.last_result.provider_model_id if self.last_result else self.model.id

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools=None,
    ) -> dict[str, Any]:
        self.last_result = await self.model.infer(
            InferenceRequest(
                messages=tuple(messages),
                tools=tuple(tools or ()),
                budget=self.budget,
            )
        )
        return self.last_result.message


def model_chat_client_for_role(
    role: str,
    *,
    budget: InferenceBudget | None = None,
) -> ModelChatAdapter:
    return ModelChatAdapter(model_for_role(role), budget=budget)
