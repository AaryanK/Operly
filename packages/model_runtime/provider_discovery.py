"""Live model discovery for configured non-OpenRouter providers.

Operly is currently operated in zero-cost mode. Provider APIs remain the source of
truth for which routes exist, but a discovered route is admitted to scoring only
when we can identify it as available on the provider's free tier/free endpoint.
Unknown-cost routes stay out of the live pool rather than risking accidental spend.
"""
from __future__ import annotations

import os
from typing import Any, Iterable

import aiohttp

from packages.model_runtime.catalog import ModelResource, provider_is_configured
from packages.model_runtime.discovery import register_model_discoverer


# Current free-plan/free-endpoint text routes verified from provider documentation.
# Provider discovery still checks that a route actually exists before registering it.
_GROQ_FREE_MODELS = frozenset(
    {
        "canopylabs/orpheus-arabic-saudi",
        "canopylabs/orpheus-v1-english",
        "groq/compound",
        "groq/compound-mini",
        "meta-llama/llama-prompt-guard-2-22m",
        "meta-llama/llama-prompt-guard-2-86m",
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "openai/gpt-oss-safeguard-20b",
        "qwen/qwen3.6-27b",
        "whisper-large-v3",
        "whisper-large-v3-turbo",
    }
)

_GEMINI_FREE_MODELS = frozenset(
    {
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-3.5-live-translate-preview",
        "gemini-3.5-transcribe-live",
        "gemini-3.5-transcribe",
    }
)

# NVIDIA's /v1/models response does not currently publish billing status. Keep only
# routes explicitly advertised by NVIDIA Build as Free Endpoint and useful to the
# shared text/reasoning/coding runtime. Unknown NVIDIA routes remain excluded.
_NVIDIA_FREE_MODELS = frozenset(
    {
        "deepseek-ai/deepseek-v4-flash-0731",
        "deepseek-ai/deepseek-v4-flash",
        "deepseek-v4-flash-0731",
        "meta/llama-3.3-70b-instruct",
        "meta/muse-glimmer-30b",
        "minimaxai/minimax-m3",
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "poolside/laguna-xs-2.1",
        "stepfun-ai/step-3.7-flash",
        "z-ai/glm-5.2",
        "zai-org/glm-5.2",
    }
)

# Ollama Free includes bounded cloud usage, but some larger cloud routes require a
# subscription. Only keep routes already verified/used as free in Operly.
_OLLAMA_FREE_MODELS = frozenset(
    {
        "gemma4:31b",
        "gpt-oss:120b",
        "gpt-oss:20b",
        "nemotron-3-super",
        "nemotron-3-nano:30b",
        "nemotron-3-ultra",
        "minimax-m3",
    }
)


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
    if any(
        token in text
        for token in (
            "reason",
            "deepseek",
            "r1",
            "qwen",
            "gpt",
            "llama",
            "gemma",
            "mistral",
            "nemotron",
            "kimi",
            "glm",
            "minimax",
        )
    ):
        caps.add("reasoning")
    if any(
        token in text
        for token in ("code", "coder", "deepseek", "qwen", "gpt", "nemotron", "kimi", "glm", "minimax")
    ):
        caps.add("coding")
    if any(token in text for token in ("vision", "vl", "multimodal", "glimmer")):
        caps.add("vision")
    if any(token in text for token in ("embed", "embedding")):
        caps = {"embeddings"}
    if "whisper" in text or "transcribe" in text:
        caps.update({"audio", "transcription"})
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


def _free_model_ids(provider: str) -> frozenset[str]:
    return {
        "groq": _GROQ_FREE_MODELS,
        "nvidia": _NVIDIA_FREE_MODELS,
        "ollama": _OLLAMA_FREE_MODELS,
    }.get(provider, frozenset())


def _openai_resource(provider: str, item: dict[str, Any]) -> ModelResource | None:
    model_id = str(item.get("id") or "").strip()
    if not model_id or model_id not in _free_model_ids(provider):
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
        free=True,
        priority=70,
        context_length=context_length,
        locality="remote",
        tags=frozenset({"discovered", "free", "zero-cost"}),
        cost_class="free",
        billing_mode="free-tier",
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
        if (
            not model_id
            or model_id not in _GEMINI_FREE_MODELS
            or not ({"generateContent", "streamGenerateContent"} & methods)
        ):
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
                free=True,
                priority=70,
                context_length=context_length,
                locality="remote",
                tags=frozenset({"discovered", "free", "zero-cost"}),
                cost_class="free",
                billing_mode="free-tier",
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
        if not model_id or model_id not in _OLLAMA_FREE_MODELS:
            continue
        resources.append(
            ModelResource(
                id=model_id,
                provider="ollama",
                name=model_id,
                canonical_id=_canonical(model_id),
                capabilities=_heuristic_capabilities(model_id),
                free=True,
                priority=70,
                locality="remote" if base.startswith("https://") else "local",
                tags=frozenset({"discovered", "free", "zero-cost"}),
                cost_class="free",
                billing_mode="free-tier" if base.startswith("https://") else "free-route",
            )
        )
    return resources


register_model_discoverer("groq", discover_groq_models, replace=True)
register_model_discoverer("gemini", discover_gemini_models, replace=True)
register_model_discoverer("nvidia", discover_nvidia_models, replace=True)
register_model_discoverer("ollama", discover_ollama_models, replace=True)
