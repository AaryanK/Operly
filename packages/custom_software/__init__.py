"""Custom-software package surface.

Do not import the live planning/model-provider stack at package import time. Runtime
protocol modules (runner contracts and source bundles) are shared with a dedicated
isolated runner and must remain dependency-light. Planning exports are resolved only
when a caller actually asks for them.
"""
from __future__ import annotations

__all__ = ["ProviderPlanningClient", "provider_planning_mode"]


def __getattr__(name: str):
    if name in {"ProviderPlanningClient", "provider_planning_mode"}:
        from .provider_planning import ProviderPlanningClient, provider_planning_mode

        return {
            "ProviderPlanningClient": ProviderPlanningClient,
            "provider_planning_mode": provider_planning_mode,
        }[name]
    raise AttributeError(name)
