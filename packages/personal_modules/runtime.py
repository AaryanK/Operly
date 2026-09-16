from __future__ import annotations

from packages.kernel.bootstrap import build_kernel_runtime
from packages.kernel.providers import CapabilityProvider
from packages.kernel.runtime_availability import AvailabilityAwareKernelRuntime
from packages.personal_modules.google_provider import (
    PROVIDER_ID as PERSONAL_GOOGLE_PROVIDER_ID,
    PersonalGoogleProvider,
    _supports as personal_google_supports,
    connector_scopes,
    personal_google_capabilities,
    personal_google_connectors,
)
from packages.workflow import (
    PROVIDER_ID as WORKFLOW_PROVIDER_ID,
    WorkflowProvider,
    personal_workflow_capabilities,
)


class PersonalRuntime(AvailabilityAwareKernelRuntime):
    """Canonical account-owned capability runtime.

    The registry is searchable, provider availability is checked before exposure, and
    execution remains behind the same Kernel policy / approval / idempotency / audit
    boundary. Personal authority never receives a synthetic Workspace ID.
    """


class RuntimePersonalGoogleProvider(PersonalGoogleProvider):
    """Production Google provider with non-secret availability diagnostics for Runtime1."""

    async def unavailability_reason(self, db, *, context, capability):
        if not context.is_personal or not context.user_id:
            return None
        if capability.id == "google.connection.status":
            return None
        rows = await personal_google_connectors(db, context.user_id)
        if any(
            personal_google_supports(capability.id, connector_scopes(row))
            for row in rows
        ):
            return None
        return "personal_google_connector_required"


def build_personal_runtime(
    *,
    personal_google_provider: CapabilityProvider | None = None,
) -> PersonalRuntime:
    """Build the canonical Personal runtime, with a narrow provider seam for fixtures.

    Production callers use the real PersonalGoogleProvider. Deterministic evaluation
    may inject a contract-compatible fake while still exercising the same registry,
    policy, idempotency, audit and Runtime1 execution path.
    """
    base = build_kernel_runtime()
    runtime = PersonalRuntime(
        registry=base.registry,
        providers=base.providers,
        policy=base.policy,
        context_loader=base.context_loader,
        planner=base.planner,
    )
    for spec in personal_google_capabilities():
        runtime.registry.register(spec)
    for spec in personal_workflow_capabilities():
        runtime.registry.register(spec)
    runtime.providers.register(
        PERSONAL_GOOGLE_PROVIDER_ID,
        personal_google_provider or RuntimePersonalGoogleProvider(),
    )
    runtime.providers.register(WORKFLOW_PROVIDER_ID, WorkflowProvider())
    return runtime
