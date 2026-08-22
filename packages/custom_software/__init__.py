"""Source-backed custom software generation vertical slice.

During the live-planning provider migration, older modules still import the legacy
``OllamaPlanningClient`` and ``planning_mode`` names from ``live_planning``. Patch
those public compatibility names at package import time so all existing call sites
use the shared model-provider registry without duplicating the planning engine.
"""

from . import live_planning as _live_planning
from .provider_planning import ProviderPlanningClient, provider_planning_mode

_live_planning.OllamaPlanningClient = ProviderPlanningClient
_live_planning.planning_mode = provider_planning_mode

__all__ = ["ProviderPlanningClient", "provider_planning_mode"]
