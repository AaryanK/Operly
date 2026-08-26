"""Temporary provider activation policy for Operly model routing.

The adaptive model index may know about many providers, but only providers explicitly
activated here may be selected or invoked. This gives operators a hard kill switch
without deleting credentials or model cards.
"""
from __future__ import annotations

import os


def active_model_providers() -> frozenset[str]:
    raw = os.getenv("OPERLY_ACTIVE_MODEL_PROVIDERS", "ollama").strip()
    return frozenset(
        item.strip().lower()
        for item in raw.split(",")
        if item.strip()
    )


def provider_is_active(provider: str) -> bool:
    return str(provider or "").strip().lower() in active_model_providers()
