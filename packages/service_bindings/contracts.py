"""Project-scoped bindings from generated software to Operly capabilities."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

JSONValue = Any


@dataclass(frozen=True, slots=True)
class ServiceBinding:
    id: str
    project_id: str
    workspace_id: str
    semantic_name: str
    capability_id: str
    capability_version: str
    binding_mode: str
    principal_scope: str
    configuration: Mapping[str, JSONValue] = field(default_factory=dict)
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class BindingCandidate:
    capability_id: str
    version: str
    display_name: str
    description: str
    risk: str
    authorized: bool | None
    configured: bool
    score: int = 0


@dataclass(frozen=True, slots=True)
class BindingInvocation:
    binding_id: str
    arguments: Mapping[str, JSONValue]
    request_id: str | None = None
