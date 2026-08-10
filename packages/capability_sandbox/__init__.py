"""Experimental capability-placement sandbox.

This package is intentionally not wired into production Studio.  It exists to
exercise general target resolution before OPERLY creates or edits software.
"""

from .target_resolution import (
    CapabilityPlacement,
    PlacementAlternative,
    WorkspaceResource,
    WorkspaceSnapshot,
    resolve_capability_placement,
    validate_placement,
)

__all__ = [
    "CapabilityPlacement",
    "PlacementAlternative",
    "WorkspaceResource",
    "WorkspaceSnapshot",
    "resolve_capability_placement",
    "validate_placement",
]
