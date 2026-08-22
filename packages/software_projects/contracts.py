"""Canonical Studio software-project contracts.

Legacy Studio, ManagedApplication, and GeneratedProject records can be adapted to
these objects while persistence is migrated incrementally.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class ProjectState(StrEnum):
    DRAFT = "draft"
    PLANNING = "planning"
    BUILDING = "building"
    PREVIEW_READY = "preview_ready"
    APPROVED = "approved"
    PUBLISHING = "publishing"
    LIVE = "live"
    DEGRADED = "degraded"
    FAILED = "failed"
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class SourceVersion:
    id: str
    project_id: str
    version: int
    files: tuple[Any, ...]
    digest: str
    parent_id: str | None = None
    summary: str = ""
    created_by: str | None = None
    created_at: datetime | None = None


@dataclass(slots=True)
class SoftwareProject:
    id: str
    workspace_id: str
    name: str
    description: str
    state: ProjectState
    active_source_version_id: str | None = None
    active_runtime_id: str | None = None
    service_binding_ids: tuple[str, ...] = ()
    created_by: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StudioSession:
    project: SoftwareProject
    source: SourceVersion | None
    runtime: Any | None
    bindings: tuple[Any, ...] = ()
    workspace_context: dict[str, Any] = field(default_factory=dict)
    selected_ui_context: dict[str, Any] = field(default_factory=dict)
