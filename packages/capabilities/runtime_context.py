from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ProviderContext:
    """Runtime-only context passed to a capability provider.

    ``tenant_id`` remains the compatibility workspace field for existing providers.
    Personal providers receive it as ``None`` and use ``owner_user_id`` instead.
    `invocation` is never part of the model-visible capability arguments and is
    intentionally not persisted as an action argument.
    """

    tenant_id: str | None
    db: Any
    actor_id: str | None = None
    provider_config: dict[str, Any] | None = None
    execution_id: str | None = None
    invocation: dict[str, Any] | None = None
    scope_kind: str = "workspace"
    owner_user_id: str | None = None

    @property
    def scope_id(self) -> str | None:
        if self.scope_kind == "personal":
            return f"personal:{self.owner_user_id}" if self.owner_user_id else None
        return self.tenant_id
