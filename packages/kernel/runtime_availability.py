from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from packages.kernel.contracts import CapabilitySpec
from packages.kernel.runtime import OperlyKernelRuntime
from packages.security.execution_context import ExecutionContext


class AvailabilityAwareKernelRuntime(OperlyKernelRuntime):
    """Kernel runtime with truthful capability exposure.

    Permission/surface checks remain owned by CapabilityRegistry. Provider preflight is
    evaluated afterwards so disabled modules and disconnected integrations are not
    exposed as currently usable tools to the Workspace UI or a future planner.
    Execution still rechecks its own provider/resource invariants.
    """

    async def available_capabilities(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        query: str | None = None,
        limit: int = 50,
    ) -> tuple[CapabilitySpec, ...]:
        if query:
            candidates = self.registry.search(
                query,
                context=context,
                effective_only=True,
                limit=max(1, min(limit, 50)),
            )
        else:
            candidates = self.registry.effective(context)

        available: list[CapabilitySpec] = []
        for spec in candidates:
            if await self.providers.is_available(db, context=context, capability=spec):
                available.append(spec)
                if len(available) >= max(1, min(limit, 500)):
                    break
        return tuple(available)
