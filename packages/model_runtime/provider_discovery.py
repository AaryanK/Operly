"""Live model discovery for configured non-OpenRouter providers.

The provider APIs remain the source of truth for which concrete routes currently
exist. Richer static/qualification cards can still override discovered metadata in
the catalog, while discovery ensures new models enter the index automatically.
"""
from __future__ import annotations

import os
from typing import Any, Iterable

import aiohttp

from packages.model_runtime.catalog import ModelResource, provider_is_configured
from packages.model_runtime.discovery import register_model_discoverer


def _first_env(names: Iterable[str]) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _timeout() -> aiohttp.ClientTimeout:
    try:
        seconds = float(os.getenv("OPERLY_MODEL_DISCOVERY_TIMEOUT_SECONDS", "15"))
    except (TypeError, ValueError):
        seconds = 15.0
    return aiohttp.ClientTimeout(total=max(2.0, min(seconds, 60.0)))


def _heuristic_capabilities(model_id: str, *, tools: bool = True) -> frozenset[str]:
    text = str(model_id or "").lower()
    caps = {"text"}
    if any(token in text for token in ("reason", "deepseek", "r1", "qwen", "gpt", "llama", "gemma", "mistral", "nemotron", "kimi")):
        caps.add("reasoning")
    if any(token in text for token in ("code", "coder", "deepseek", "qwen", "gpt", "nemotron", "kimi")):
        caps.add("coding")
    if any(token in text for token in ("vision", "vl", "multimodal")):
        caps.add("vision")
    if any(token in text for token in ("embed", "embedding")):
        caps = {"embeddings"}
    if tools and "embeddings" not in caps:
        caps.add("tools")
    return frozenset(caps)


def _canonical(model_id: str) -> str:
    value = str(model_id).strip()
    lowered = value.lower()
    if "deepseek" in lowered:
        suffix = lowered.split("deepseek", 1)[1].lstrip("-_/:")
        return f"deepseek:{suffix or lowered}"
    if "gpt-oss" in lowered:
        suffix = lowered.split("gpt-oss", 1)[1].lstrip("-_/:")
        return f"openai:gpt-oss-{suffix}" if suffix else "openai:gpt-oss"
    return value


def _openai_resource(provider: str, item: dict[str, Any]) -> ModelResource | None:
    model_id = str(item.get("id") or "").strip()
    if not model_id:
        return None
    context = item.get("max_model_len") or item.get("context_window") or item.get("context_length")
    try:
        context_length = int(context) if context is not None else None
    except (TypeError, ValueError):
        context_length = None
    return ModelResource(
        id=model_id,
        provider=provider,
        name=str(item.get("name") or model_id).strip(),
        canonical_id=_canonical(model_id),
        capabilities=_heuristic_capabilities(model_id),
        priority=70,
        context_length=context_length,
        locality="remote",
        tags=frozenset({"discovered"}),
    )


async def _discover_openai_models(
    provider: str,
    *,
    url: str,
    key_envs: tuple[str, ...],
) -> list[ModelResource]:
    if not provider_is_configured(provider):
        return []
    key = _first_env(key_envs)
    headers = {"Accept": "application/json", "Authorization": f"Bearer {key}"}
    async with aiohttp.ClientSession(timeout=_timeout()) as session:
        async with session.get(url, headers=headers) as response:
            if response.status >= 400:
                raise RuntimeError(f"{provider} model discovery failed with status {response.status}")
            payload = await response.json()
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError(f"{provider} model discovery returned an invalid catalog")
    resources = [_openai_resource(provider, row) for row in rows if isinstance(row, dict)]
    return [resource for resource in resources if resource is not None]


async def discover_groq_models() -> list[ModelResource]:
    return await _discover_openai_models(
        "groq",
        url=os.getenv("GROQ_MODELS_URL", "https://api.groq.com/openai/v1/models").strip(),
        key_envs=("GROQ_API_KEY", "groq_api_key"),
    )


async def discover_nvidia_models() -> list[ModelResource]:
    # NVIDIA NIM exposes the standard OpenAI-compatible GET /v1/models endpoint.
    return await _discover_openai_models(
        "nvidia",
        url=os.getenv("NVIDIA_MODELS_URL", "https://integrate.api.nvidia.com/v1/models").strip(),
        key_envs=("NVIDIA_API_KEY", "nvidia_api_key"),
    )


async def discover_gemini_models() -> list[ModelResource]:
    if not provider_is_configured("gemini"):
        return []
    key = _first_env(("GEMINI_API_KEY", "gemini_api_key", "GOOGLE_API_KEY"))
    url = os.getenv("GEMINI_MODELS_URL", "https://generativelanguage.googleapis.com/v1beta/models").strip()
    headers = {"Accept": "application/json", "x-goog-api-key": key}
    async with aiohttp.ClientSession(timeout=_timeout()) as session:
        async with session.get(url, headers=headers) as response:
            if response.status >= 400:
                raise RuntimeError(f"gemini model discovery failed with status {response.status}")
            payload = await response.json()
    rows = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("gemini model discovery returned an invalid catalog")
    resources: list[ModelResource] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        raw_name = str(item.get("name") or "").strip()
        model_id = raw_name.removeprefix("models/")
        methods = {str(value) for value in item.get("supportedGenerationMethods") or []}
        if not model_id or not ({"generateContent", "streamGenerateContent"} & methods):
            continue
        context = item.get("inputTokenLimit")
        try:
            context_length = int(context) if context is not None else None
        except (TypeError, ValueError):
            context_length = None
        resources.append(
            ModelResource(
                id=model_id,
                provider="gemini",
                name=str(item.get("displayName") or model_id).strip(),
                canonical_id=f"google:{model_id}",
                capabilities=_heuristic_capabilities(model_id),
                priority=70,
                context_length=context_length,
                locality="remote",
                tags=frozenset({"discovered"}),
            )
        )
    return resources


async def discover_ollama_models() -> list[ModelResource]:
    if not provider_is_configured("ollama"):
        return []
    configured_chat = os.getenv("OLLAMA_URL", "https://ollama.com/api/chat").strip()
    base = configured_chat.split("/api/", 1)[0].rstrip("/")
    url = os.getenv("OLLAMA_MODELS_URL", f"{base}/api/tags").strip()
    key = _first_env(("OLLAMA_API_KEY", "ollama_api_key"))
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    async with aiohttp.ClientSession(timeout=_timeout()) as session:
        async with session.get(url, headers=headers) as response:
            if response.status >= 400:
                raise RuntimeError(f"ollama model discovery failed with status {response.status}")
            payload = await response.json()
    rows = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("ollama model discovery returned an invalid catalog")
    resources: list[ModelResource] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("name") or item.get("model") or "").strip()
        if not model_id:
            continue
        resources.append(
            ModelResource(
                id=model_id,
                provider="ollama",
                name=model_id,
                canonical_id=_canonical(model_id),
                capabilities=_heuristic_capabilities(model_id),
                priority=70,
                locality="remote" if base.startswith("https://") else "local",
                tags=frozenset({"discovered"}),
            )
        )
    return resources


register_model_discoverer("groq", discover_groq_models, replace=True)
register_model_discoverer("gemini", discover_gemini_models, replace=True)
register_model_discoverer("nvidia", discover_nvidia_models, replace=True)
register_model_discoverer("ollama", discover_ollama_models, replace=True)
