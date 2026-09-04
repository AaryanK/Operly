from __future__ import annotations

from packages.kernel.bootstrap import build_kernel_runtime
from packages.kernel.runtime_availability import AvailabilityAwareKernelRuntime
from packages.personal_modules.google_provider import (
    PROVIDER_ID as PERSONAL_GOOGLE_PROVIDER_ID,
    PersonalGoogleProvider,
    personal_google_capabilities,
)


class PersonalRuntime(AvailabilityAwareKernelRuntime):
    """Canonical account-owned capability runtime.

    This is deliberately parallel to WorkspaceRuntime: the registry is searchable,
    provider availability is checked before a tool is exposed, and execution remains
    behind the same Kernel policy / approval / idempotency / audit boundary. Personal
    authority never receives a synthetic Workspace ID.
    """


def build_personal_runtime() -> PersonalRuntime:
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
    runtime.providers.register(PERSONAL_GOOGLE_PROVIDER_ID, PersonalGoogleProvider())
    return runtime
