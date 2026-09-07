from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from packages.kernel.capability_search import CapabilitySearchQuery, tokens
from packages.kernel.contracts import CapabilitySpec
from packages.kernel.runtime import OperlyKernelRuntime
from packages.security.execution_context import ExecutionContext


def _family(capability_id: str) -> str:
    parts = [part for part in str(capability_id or "").split(".") if part]
    if len(parts) <= 1:
        return str(capability_id or "")
    return ".".join(parts[:-1])


def _resource_overlap(spec: CapabilitySpec, resource_terms: frozenset[str]) -> int:
    if not resource_terms:
        return 0
    text = " ".join(
        (
            spec.id,
            str(spec.display_name or ""),
            str(spec.description or ""),
            *sorted(spec.tags),
            str(spec.resource_scope or ""),
        )
    )
    return len(resource_terms & frozenset(tokens(text)))


def _focus_resource_family(
    query: str,
    candidates: tuple[CapabilitySpec, ...],
) -> tuple[CapabilitySpec, ...]:
    """Drop operation-only noise once a resource-specific capability family is found.

    Capability search intentionally has broad operation recall. A generic verb like
    "read" can therefore seed many unrelated read-only contracts. The model-compiled
    resource hints are a stronger signal. When at least one candidate matches those
    hints, keep the strongest resource matches plus siblings in their local capability
    family so multi-step search -> read flows remain possible.
    """

    parsed = CapabilitySearchQuery.parse(query)
    if not parsed.resource_terms or len(candidates) <= 1:
        return candidates

    overlaps = {
        spec.id: _resource_overlap(spec, parsed.resource_terms)
        for spec in candidates
    }
    best = max(overlaps.values(), default=0)
    if best <= 0:
        return candidates

    anchor_ids = {
        capability_id
        for capability_id, value in overlaps.items()
        if value == best
    }
    families = {
        _family(spec.id)
        for spec in candidates
        if spec.id in anchor_ids
    }
    focused = tuple(
        spec
        for spec in candidates
        if spec.id in anchor_ids or _family(spec.id) in families
    )
    return focused or candidates


class AvailabilityAwareKernelRuntime(OperlyKernelRuntime):
    """Kernel runtime with truthful, resource-focused capability exposure."""

    async def available_capabilities(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        query: str | None = None,
        limit: int = 50,
    ) -> tuple[CapabilitySpec, ...]:
        bounded_limit = max(1, min(limit, 50))
        if query:
            # Retrieve a wider bounded pool first; resource-family focus happens only
            # after trusted registry permission/surface filtering.
            candidates = self.registry.search(
                query,
                context=context,
                effective_only=True,
                limit=max(bounded_limit, min(50, bounded_limit * 4)),
            )
            candidates = _focus_resource_family(query, candidates)
        else:
            candidates = self.registry.effective(context)

        available: list[CapabilitySpec] = []
        for spec in candidates:
            if await self.providers.is_available(
                db,
                context=context,
                capability=spec,
            ):
                available.append(spec)
                if len(available) >= bounded_limit:
                    break
        return tuple(available)
