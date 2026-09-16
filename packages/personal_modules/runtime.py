from __future__ import annotations

from packages.kernel.bootstrap import build_kernel_runtime
from packages.kernel.providers import CapabilityProvider
from packages.kernel.runtime_availability import AvailabilityAwareKernelRuntime
from packages.personal_modules.google_completion_provider import (
    PersonalGoogleCompletionProvider,
    completion_google_capabilities,
)
from packages.personal_modules.google_provider import (
    PROVIDER_ID as PERSONAL_GOOGLE_PROVIDER_ID,
    PersonalGoogleProvider,
    personal_google_capabilities,
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


def build_personal_runtime(
    *,
    personal_google_provider: CapabilityProvider | None = None,
) -> PersonalRuntime:
    """Build the canonical Personal runtime, with a narrow provider seam for fixtures.

    Production callers use the Google completion provider, which extends the existing
    PersonalGoogleProvider with contact lookup and saved-draft read-back. Deterministic
    evaluation may inject a contract-compatible fake while still exercising the same
    registry, policy, idempotency, audit and Runtime1 execution path.
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
    for spec in completion_google_capabilities():
        runtime.registry.register(spec)
    for spec in personal_workflow_capabilities():
        runtime.registry.register(spec)
    runtime.providers.register(
        PERSONAL_GOOGLE_PROVIDER_ID,
        personal_google_provider or PersonalGoogleCompletionProvider(),
    )
    runtime.providers.register(WORKFLOW_PROVIDER_ID, WorkflowProvider())
    return runtime
