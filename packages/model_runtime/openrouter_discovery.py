"""OpenRouter model-catalog discovery plugin.

This module translates OpenRouter marketplace metadata into Operly's provider-
agnostic ModelResource contract. Harnesses never import this module directly.
"""
from __future__ import annotations

import os
from typing import Any

import aiohttp

from packages.model_runtime.catalog import ModelResource, provider_is_configured
from packages.model_runtime.discovery import register_model_discoverer


def _number(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _is_free(model_id: str, pricing: dict[str, Any]) -> bool:
    if str(model_id).endswith(":free"):
        return True
    core = [
        _number(pricing.get(key))
        for key in ("prompt", "completion", "input", "output")
        if key in pricing
    ]
    return bool(core) and all(value == 0.0 for value in core if value is not None)


def _per_million(pricing: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _number(pricing.get(key))
        if value is not None:
            return value * 1_000_000.0
    return None


def _capabilities(item: dict[str, Any]) -> frozenset[str]:
    architecture = item.get("architecture") or {}
    inputs = {
        str(value).strip().lower()
        for value in architecture.get("input_modalities") or []
        if str(value).strip()
    }
    outputs = {
        str(value).strip().lower()
        for value in architecture.get("output_modalities") or []
        if str(value).strip()
    }
    parameters = {
        str(value).strip()
        for value in item.get("supported_parameters") or []
        if str(value).strip()
    }
    capabilities: set[str] = set()
    if "text" in inputs or "text" in outputs:
        capabilities.add("text")
    if "image" in inputs:
        capabilities.update({"vision", "image_input"})
    if "video" in inputs:
        capabilities.update({"video", "video_input"})
    if "audio" in inputs:
        capabilities.update({"audio", "audio_input"})
    if "file" in inputs:
        capabilities.add("file_input")
    if "image" in outputs:
        capabilities.update({"image_generation", "image_output"})
    if "audio" in outputs:
        capabilities.update({"audio_generation", "audio_output"})
    if "video" in outputs:
        capabilities.update({"video_generation", "video_output"})
    if "embedding" in outputs or "embeddings" in outputs:
        capabilities.add("embeddings")
    if "tools" in parameters or "tool_choice" in parameters:
        capabilities.add("tools")
    if {"reasoning", "include_reasoning", "reasoning_effort"} & parameters:
        capabilities.add("reasoning")
    if "structured_outputs" in parameters or "response_format" in parameters:
        capabilities.add("structured_output")
    searchable = " ".join([
        str(item.get("name") or ""),
        str(item.get("description") or ""),
        str(item.get("id") or ""),
    ]).lower()
    if any(token in searchable for token in ("coding", "coder", "software engineering", "code generation")):
        capabilities.add("coding")
    if "translat" in searchable:
        capabilities.add("translation")
    if "rerank" in searchable:
        capabilities.add("reranking")
    if "transcription" in searchable or "speech-to-text" in searchable:
        capabilities.add("transcription")
    if "text-to-speech" in searchable or "speech synthesis" in searchable:
        capabilities.add("speech")
    return frozenset(capabilities)


def resource_from_openrouter_model(item: dict[str, Any]) -> ModelResource | None:
    model_id = str(item.get("id") or "").strip()
    if not model_id:
        return None
    architecture = item.get("architecture") or {}
    input_modalities = frozenset(
        str(value).strip().lower()
        for value in architecture.get("input_modalities") or []
        if str(value).strip()
    )
    output_modalities = frozenset(
        str(value).strip().lower()
        for value in architecture.get("output_modalities") or []
        if str(value).strip()
    )
    parameters = frozenset(
        str(value).strip()
        for value in item.get("supported_parameters") or []
        if str(value).strip()
    )
    pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
    context_length = item.get("context_length")
    try:
        normalized_context = int(context_length) if context_length is not None else None
    except (TypeError, ValueError):
        normalized_context = None
    free = _is_free(model_id, pricing)
    return ModelResource(
        id=model_id,
        provider="openrouter",
        name=str(item.get("name") or model_id).strip(),
        canonical_id=model_id.removesuffix(":free"),
        capabilities=_capabilities(item),
        free=free,
        priority=50,
        context_length=normalized_context,
        input_modalities=input_modalities,
        output_modalities=output_modalities,
        supported_parameters=parameters,
        tags=frozenset({"discovered"}),
        locality="remote",
        billing_mode="free-route" if free else "usage",
        input_cost_per_million=_per_million(pricing, "prompt", "input"),
        output_cost_per_million=_per_million(pricing, "completion", "output"),
    )


async def discover_openrouter_models() -> list[ModelResource]:
    if not provider_is_configured("openrouter"):
        return []
    url = os.getenv("OPEN_ROUTER_MODELS_URL", "https://openrouter.ai/api/v1/models").strip()
    timeout = max(2.0, min(float(os.getenv("OPEN_ROUTER_DISCOVERY_TIMEOUT_SECONDS", "15")), 60.0))
    key = (
        os.getenv("OPEN_ROUTER_API", "").strip()
        or os.getenv("OPENROUTER_API_KEY", "").strip()
        or os.getenv("OPEN_ROUTER_API_KEY", "").strip()
        or os.getenv("openrouter_api_key", "").strip()
    )
    headers = {"Accept": "application/json", "Authorization": f"Bearer {key}"}
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.get(url, headers=headers) as response:
            if response.status >= 400:
                raise RuntimeError(f"OpenRouter model discovery failed with status {response.status}")
            payload = await response.json()
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("OpenRouter model discovery returned an invalid catalog")
    resources = [resource_from_openrouter_model(item) for item in rows if isinstance(item, dict)]
    return [resource for resource in resources if resource is not None]


register_model_discoverer("openrouter", discover_openrouter_models, replace=True)
