from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AgentInput:
    tenant_id: str
    principal_id: str
    actor_name: str
    channel: str
    text: str
    conversation_id: str | None = None
    images: list[str] = field(default_factory=list)
    attachment_context: str = ""
    attachment_names: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolContext:
    tenant_id: str
    principal_id: str
    actor_name: str
    channel: str
    conversation_id: str
    metadata: dict[str, Any]
