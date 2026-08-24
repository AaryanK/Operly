from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ProviderContext:
    """Runtime-only context passed to a capability provider.

    ``tenant_id`` remains the workspace Tenant ID for workspace execution. Personal
    execution keeps it ``None`` and carries its account namespace separately through
    ``scope_kind``, ``scope_id`` and ``owner_user_id``. These fields are populated by
    the Action lifecycle from durable, validated ownership state rather than model
    arguments or caller metadata.

    `invocation` is never part of the model-visible capability arguments and is
    intentionally not persisted as an action argument. It is suitable for
    ephemeral channel metadata used only by AUTO capabilities.
    """

    tenant_id: str | None
    db: Any
    actor_id: str | None = None
    provider_config: dict[str, Any] | None = None
    execution_id: str | None = None
    invocation: dict[str, Any] | None = None
    scope_kind: str = "workspace"
    scope_id: str | None = None
    owner_user_id: str | None = None

    @property
    def is_personal(self) -> bool:
        return self.scope_kind == "personal"

    @property
    def is_workspace(self) -> bool:
        return self.scope_kind == "workspace"
