from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ProviderContext:
    """Runtime-only context passed to a capability provider.

    `invocation` is never part of the model-visible capability arguments and is
    intentionally not persisted as an action argument. It is suitable for
    ephemeral channel metadata used only by AUTO capabilities.
    """

    tenant_id: str
    db: Any
    actor_id: str | None = None
    provider_config: dict[str, Any] | None = None
    execution_id: str | None = None
    invocation: dict[str, Any] | None = None
