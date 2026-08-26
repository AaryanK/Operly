"""Compatibility tombstone for the removed Studio-specific agent runtime.

Studio no longer owns an objective controller, model loop, retry loop, or completion
state. Active software generation uses the Agent Factory/canonical AgentRuntime and the
generic coding/runner services. This module exists only so stale imports fail clearly
instead of silently reintroducing a second runtime.
"""
from __future__ import annotations

from typing import Any


def source_scoped_idempotency_key(base: str, source: Any) -> str:
    """Bind a runner idempotency key to one immutable source bundle."""
    version = int(getattr(source, "source_version", 0) or 0)
    digest = str(getattr(source, "bundle_digest", "") or "").replace("sha256:", "")
    identity = digest[:20] or str(getattr(source, "id", "unknown"))[:20]
    return f"{base}:source:{version}:{identity}"


async def run_studio_generation(*_args, **_kwargs):
    """Fail closed if any stale caller tries to revive the old Studio runtime."""
    raise RuntimeError(
        "Studio-specific agent orchestration was removed. Route software work through "
        "the canonical Agent Factory/AgentRuntime and generic coding runner."
    )
