"""Progressive capability exposure for one model session."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from packages.model_runtime.trace_context import current_trace_metadata


DEFAULT_KERNEL_IDS = frozenset(
    {
        "capability.search",
        "capability.describe",
        "event.search",
        "event.describe",
        "context.search",
        "context.get",
        "model.invoke",
        "model.deep_reason",
    }
)

# Canonical product-level operations that should be available immediately when the
# authenticated principal is allowed to use them. Unlike DEFAULT_KERNEL_IDS these do
# not bypass capability-stage narrowing; they are ordinary governed operations whose
# schemas simply do not require a discovery round trip first.
DEFAULT_ROOT_OPERATION_IDS = frozenset({"software.build"})

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
    with a tiny permanent kernel plus first-class root operations and explicitly
    supplied initial IDs, then expands only after discovery/describe observations.
    """

    registry: Any
    tenant_id: str
    authority: set[str]
    visible_predicate: Callable[[str], bool] | None = None
    initial_ids: Iterable[str] = ()
    exposed_ids: set[str] = field(default_factory=set)
    pending_discovery_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.exposed_ids.update(DEFAULT_KERNEL_IDS)
        self.exposed_ids.update(DEFAULT_ROOT_OPERATION_IDS)
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
        return definition.risk_level == "read_only"

    def schemas(self, *, stage: str | None = None) -> list[dict[str, Any]]:
        metadata = current_trace_metadata()
        effective_stage = str(stage or metadata.get("capability_stage") or "adaptive")
        schemas = []
        pending = ", ".join(self.pending_discovery_ids[:8])
        for capability_id in sorted(self.exposed_ids):
            if not self._visible(capability_id):
                continue
            definition = self.registry.definition(capability_id)
            if not self._stage_allows(definition, effective_stage):
                continue
            schema = definition.model_tool_schema()
            if pending and capability_id in {"capability.search", "capability.describe"}:
                function = schema.get("function") if isinstance(schema, dict) else None
                if isinstance(function, dict):
                    description = str(function.get("description") or "").rstrip()
                    if capability_id == "capability.search":
                        function["description"] = (
                            description
                            + " Recent search already found sufficient candidates: "
                            + pending
                            + ". Describe/use those candidates before searching again unless they prove unavailable or unsuitable."
                        )
                    else:
                        function["description"] = (
                            description
                            + " Recent sufficient search candidates awaiting schema inspection: "
                            + pending
                            + "."
                        )
            schemas.append(schema)
        return schemas

    def expose(self, capability_ids: Iterable[str]) -> None:
        for capability_id in capability_ids:
            clean = str(capability_id or "").strip()
            if clean and self._visible(clean):
                self.exposed_ids.add(clean)

    def observe(self, capability_id: str, invocation_result: dict[str, Any]) -> None:
        """Track discovery progress and expose exact schemas only after describe.

        capability.search returns metadata only. When the search reports a sufficient
        candidate set, remember those IDs so the next tool surface tells the model to
        inspect/use them instead of immediately paying for another search. describe is
        still the only transition from discovered metadata to executable model schema.
        """
        observation = invocation_result.get("observation")
        if not isinstance(observation, dict):
            return

        if capability_id == "capability.search":
            if (
                observation.get("sufficient_match") is True
                and observation.get("search_again_recommended") is False
            ):
                ranked = observation.get("ranked_ids") or []
                if not isinstance(ranked, list):
                    ranked = []
                self.pending_discovery_ids = tuple(
                    str(item).strip()
                    for item in ranked[:8]
                    if str(item).strip()
                )
            else:
                self.pending_discovery_ids = ()
            return

        if capability_id != "capability.describe":
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
        if ids:
            self.pending_discovery_ids = ()