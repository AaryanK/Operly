"""Progressive capability exposure for one model session."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from packages.model_runtime.trace_context import current_trace_metadata


DEFAULT_KERNEL_IDS = frozenset(
    {
        "capability.search",
        "capability.describe",
        "context.search",
        "context.get",
        "model.invoke",
    }
)

_READ_METHOD_MARKERS = (
    ".read",
    ".get",
    ".list",
    ".search",
    ".inspect",
    ".query",
    ".context",
    ".freebusy",
    ".status",
    ".describe",
)


@dataclass(slots=True)
class SessionCapabilityView:
    """The exact model-visible capability surface for one authorized session.

    Authorization and relevance are deliberately separate. A capability can be
    executable by the principal without being exposed to the model. The view starts
    with a tiny permanent kernel plus explicitly supplied initial IDs, then expands
    only after discovery/describe observations.
    """

    registry: Any
    tenant_id: str
    authority: set[str]
    visible_predicate: Callable[[str], bool] | None = None
    initial_ids: Iterable[str] = ()
    exposed_ids: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.exposed_ids.update(DEFAULT_KERNEL_IDS)
        self.exposed_ids.update(str(item) for item in self.initial_ids if str(item))

    def _visible(self, capability_id: str) -> bool:
        if self.visible_predicate and not self.visible_predicate(capability_id):
            return False
        try:
            definition = self.registry.definition(capability_id)
            self.registry.resolve(
                self.tenant_id,
                definition.id,
                authority=self.authority,
            )
        except (LookupError, PermissionError):
            return False
        return True

    @staticmethod
    def _stage_allows(definition, stage: str) -> bool:
        """Reduce model-visible authority without ever granting new authority."""
        normalized = str(stage or "adaptive").strip().lower()
        if normalized in {"", "adaptive", "execution", "execute"}:
            return True
        if definition.id in DEFAULT_KERNEL_IDS:
            return True
        if normalized in {"research", "calendar", "proposal", "planning"}:
            return definition.risk_level == "read_only"
        if normalized in {"verification", "verify"}:
            if definition.risk_level == "read_only":
                return True
            capability_id = str(definition.id or "").lower()
            return capability_id.startswith(("action.", "task.")) and any(
                marker in capability_id for marker in _READ_METHOD_MARKERS
            )
        # Unknown stages fail closed for mutating capabilities while preserving
        # observation/discovery so the model can recover rather than execute.
        return definition.risk_level == "read_only"

    def schemas(self, *, stage: str | None = None) -> list[dict[str, Any]]:
        metadata = current_trace_metadata()
        effective_stage = str(stage or metadata.get("capability_stage") or "adaptive")
        schemas = []
        for capability_id in sorted(self.exposed_ids):
            if not self._visible(capability_id):
                continue
            definition = self.registry.definition(capability_id)
            if not self._stage_allows(definition, effective_stage):
                continue
            schemas.append(definition.model_tool_schema())
        return schemas

    def expose(self, capability_ids: Iterable[str]) -> None:
        for capability_id in capability_ids:
            clean = str(capability_id or "").strip()
            if clean and self._visible(clean):
                self.exposed_ids.add(clean)

    def observe(self, capability_id: str, invocation_result: dict[str, Any]) -> None:
        """Expand exact schemas only after capability.describe.

        capability.search intentionally returns metadata only. describe is the
        transition from "discoverable" to "model-visible schema" and still cannot
        grant authority because expose() rechecks the session visibility predicate.
        """
        if capability_id != "capability.describe":
            return
        observation = invocation_result.get("observation")
        if not isinstance(observation, dict):
            return
        rows = observation.get("capabilities") or []
        if not isinstance(rows, list):
            return
        ids = [
            str(row.get("id") or "")
            for row in rows
            if isinstance(row, dict) and row.get("authorized") is not False
        ]
        self.expose(ids)
