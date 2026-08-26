"""Zero-cost eligibility policy for Operly model routing.

A model is routable only when its provider/model route is known to be free under
the current provider plan. This is intentionally fail-closed: unknown billing
status means ineligible, even if the model would otherwise score highly.
"""
from __future__ import annotations

import os
from typing import Protocol


class FreeRoute(Protocol):
    provider: str
    provider_model_id: str
    tags: frozenset[str]


GROQ_FREE_MODELS = frozenset(
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

GEMINI_FREE_MODELS = frozenset(
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

NVIDIA_FREE_MODELS = frozenset(
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

OLLAMA_FREE_MODELS = frozenset(
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


def free_only_enabled() -> bool:
    value = os.getenv("OPERLY_FREE_MODELS_ONLY", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def route_is_zero_cost(model: FreeRoute) -> bool:
    if not free_only_enabled():
        return True
    provider = str(getattr(model, "provider", "") or "").strip().lower()
    model_id = str(getattr(model, "provider_model_id", "") or "").strip()
    tags = set(getattr(model, "tags", frozenset()) or ())
    if "free" not in tags:
        return False
    if provider == "openrouter":
        # OpenRouter discovery derives this tag from zero token pricing / :free.
        return True
    if provider == "groq":
        return model_id in GROQ_FREE_MODELS
    if provider == "gemini":
        return model_id in GEMINI_FREE_MODELS
    if provider == "nvidia":
        return model_id in NVIDIA_FREE_MODELS
    if provider == "ollama":
        return model_id in OLLAMA_FREE_MODELS
    # New providers are fail-closed until their free-tier semantics are added here.
    return False
