from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PrincipalKind(StrEnum):
    """Trusted actor kinds that may enter the governed Operly capability fabric.

    Resource scope (personal/workspace) is intentionally separate from principal kind.
    A workflow or generated production can therefore operate inside a workspace without
    impersonating the human who originally created it.
    """

    USER = "user"
    AGENT_RUN = "agent_run"
    WORKFLOW = "workflow"
    SOFTWARE_PROJECT = "software_project"
    PRODUCTION = "production"
    APP_USER = "app_user"
    PUBLIC_SESSION = "public_session"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class PrincipalRef:
    kind: PrincipalKind
    id: str
    parent: "PrincipalRef | None" = None
    delegation_id: str | None = None

    def __post_init__(self) -> None:
        value = str(self.id or "").strip()
        if not value:
            raise ValueError("Principal id is required")
        if len(value) > 240:
            raise ValueError("Principal id is too long")
        object.__setattr__(self, "id", value)

    @property
    def key(self) -> str:
        return f"{self.kind.value}:{self.id}"

    def ancestry(self, *, limit: int = 8) -> tuple[str, ...]:
        output: list[str] = []
        current: PrincipalRef | None = self
        while current is not None and len(output) < max(1, min(limit, 16)):
            output.append(current.key)
            current = current.parent
        return tuple(output)


def user_principal(user_id: str) -> PrincipalRef:
    return PrincipalRef(PrincipalKind.USER, user_id)
