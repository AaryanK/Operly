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
    """Drop operation-only noise once a resource-specific capability family is found."""

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

    def _candidates(
        self,
        *,
        context: ExecutionContext,
        query: str | None,
        limit: int,
    ) -> tuple[CapabilitySpec, ...]:
        bounded_limit = max(1, min(limit, 50))
        if query:
            candidates = self.registry.search(
                query,
                context=context,
                effective_only=True,
                limit=max(bounded_limit, min(50, bounded_limit * 4)),
            )
            return _focus_resource_family(query, candidates)
        return self.registry.effective(context)

    async def available_capabilities(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        query: str | None = None,
        limit: int = 50,
    ) -> tuple[CapabilitySpec, ...]:
        bounded_limit = max(1, min(limit, 50))
        candidates = self._candidates(context=context, query=query, limit=bounded_limit)

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

    async def availability_blocker_code(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        query: str,
        limit: int = 50,
    ) -> str | None:
        """Explain why authorized candidates are hidden, without exposing them.

        The method is called only after normal availability filtering returns no tools.
        If trusted registry search found no effective candidates, the caller should keep
        the ordinary no-authorized-capability result. If candidates exist but providers
        hide all of them, a provider may return a stable blocker code. Conflicting or
        unexplained reasons collapse to a generic dependency-unavailable code.
        """

        candidates = self._candidates(context=context, query=query, limit=limit)
        if not candidates:
            return None

        reasons: list[str] = []
        unavailable_count = 0
        for spec in candidates:
            if await self.providers.is_available(
                db,
                context=context,
                capability=spec,
            ):
                return None
            unavailable_count += 1
            reason = await self.providers.unavailability_reason(
                db,
                context=context,
                capability=spec,
            )
            if reason:
                reasons.append(reason)

        if unavailable_count == 0:
            return None
        unique = set(reasons)
        if len(unique) == 1 and len(reasons) == unavailable_count:
            return reasons[0]
        return "capability_dependency_unavailable"
