"""Canonical user-facing lifecycle states for capability execution.

The database keeps a few fine-grained internal states (EXECUTING/EXECUTED/
VERIFYING and REJECTED) for compatibility.  Model/tool responses must use this
smaller truthful vocabulary so an intermediate state can never be presented as a
completed action.
"""
from __future__ import annotations

from enum import StrEnum


class LifecycleStatus(StrEnum):
    PROPOSED = "PROPOSED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    UNVERIFIED = "UNVERIFIED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


_INTERNAL_TO_CANONICAL = {
    "PROPOSED": LifecycleStatus.PROPOSED,
    "WAITING_APPROVAL": LifecycleStatus.WAITING_APPROVAL,
    "APPROVED": LifecycleStatus.APPROVED,
    "EXECUTING": LifecycleStatus.RUNNING,
    "EXECUTED": LifecycleStatus.RUNNING,
    "VERIFYING": LifecycleStatus.RUNNING,
    "RUNNING": LifecycleStatus.RUNNING,
    "VERIFIED": LifecycleStatus.VERIFIED,
    "FAILED": LifecycleStatus.FAILED,
    "VERIFICATION_FAILED": LifecycleStatus.UNVERIFIED,
    "UNVERIFIED": LifecycleStatus.UNVERIFIED,
    "REJECTED": LifecycleStatus.CANCELLED,
    "CANCELLED": LifecycleStatus.CANCELLED,
    "EXPIRED": LifecycleStatus.EXPIRED,
}


def normalize_lifecycle_status(status: object) -> LifecycleStatus:
    value = str(getattr(status, "value", status) or "").strip().upper()
    return _INTERNAL_TO_CANONICAL.get(value, LifecycleStatus.UNVERIFIED)


def lifecycle_truth(status: object) -> dict[str, bool]:
    """Facts final-response code can safely use without inventing completion."""
    normalized = normalize_lifecycle_status(status)
    return {
        "terminal": normalized
        in {
            LifecycleStatus.VERIFIED,
            LifecycleStatus.FAILED,
            LifecycleStatus.UNVERIFIED,
            LifecycleStatus.CANCELLED,
            LifecycleStatus.EXPIRED,
        },
        "completed": normalized == LifecycleStatus.VERIFIED,
        "awaiting_approval": normalized == LifecycleStatus.WAITING_APPROVAL,
        "running": normalized == LifecycleStatus.RUNNING,
        "verified": normalized == LifecycleStatus.VERIFIED,
    }
