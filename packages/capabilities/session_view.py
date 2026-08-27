"""Progressive, namespace-first capability exposure for one model session."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from packages.model_runtime.trace_context import current_trace_metadata


# The model gets navigation, not the entire execution catalog. runtime.context is
# supplied explicitly by the harness because it depends on the concrete surface.
DEFAULT_KERNEL_IDS = frozenset(
    {
        "capability.search",
        "capability.expand",
        "capability.describe",
    }
)

# Kept as a compatibility symbol for callers/tests. Product operations no longer
# bypass namespace discovery.
DEFAULT_ROOT_OPERATION_IDS = frozenset()

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
    ".expand",
)


@dataclass(slots=True)
class SessionCapabilityView:
    """Exact model-visible capability surface for one authorized session.

    Authorization and relevance remain separate.  The view starts with only the
    namespace-navigation kernel plus explicitly supplied surface primitives.  Exact
    operation schemas appear only after a namespace-local capability.describe result.
    """

    registry: Any
    tenant_id: str
    authority: set[str]
    visible_predicate: Callable[[str], bool] | None = None
    initial_ids: Iterable[str] = ()
    exposed_ids: set[str] = field(default_factory=set)
    pending_discovery_ids: tuple[str, ...] = ()
    pending_leaf_ids: tuple[str, ...] = ()

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
        pending_namespaces = ", ".join(self.pending_discovery_ids[:6])
        pending_leaves = ", ".join(self.pending_leaf_ids[:8])
        for capability_id in sorted(self.exposed_ids):
            if not self._visible(capability_id):
                continue
            definition = self.registry.definition(capability_id)
            if not self._stage_allows(definition, effective_stage):
                continue
            schema = definition.model_tool_schema()
            function = schema.get("function") if isinstance(schema, dict) else None
            if isinstance(function, dict):
                description = str(function.get("description") or "").rstrip()
                if capability_id == "capability.search" and pending_namespaces:
                    function["description"] = (
                        description
                        + " Recent namespace matches: "
                        + pending_namespaces
                        + ". Expand one of those before searching again unless none fits."
                    )
                elif capability_id == "capability.expand" and pending_namespaces:
                    function["description"] = (
                        description
                        + " Current likely namespace paths: "
                        + pending_namespaces
                        + "."
                    )
                elif capability_id == "capability.describe" and pending_leaves:
                    function["description"] = (
                        description
                        + " The last expansion mounted these operation IDs: "
                        + pending_leaves
                        + ". Describe only the operation(s) needed."
                    )
            schemas.append(schema)
        return schemas

    def expose(self, capability_ids: Iterable[str]) -> None:
        for capability_id in capability_ids:
            clean = str(capability_id or "").strip()
            if clean and self._visible(clean):
                self.exposed_ids.add(clean)

    def observe(self, capability_id: str, invocation_result: dict[str, Any]) -> None:
        """Advance search -> expand -> describe without exposing unrelated leaves."""
        observation = invocation_result.get("observation")
        if not isinstance(observation, dict):
            return

        if capability_id == "capability.search":
            ranked = observation.get("ranked_namespace_ids") or []
            if not isinstance(ranked, list):
                ranked = []
            self.pending_discovery_ids = tuple(
                str(item).strip() for item in ranked[:6] if str(item).strip()
            )
            self.pending_leaf_ids = ()
            return

        if capability_id == "capability.expand":
            mounted = observation.get("capability_ids") or []
            if not isinstance(mounted, list):
                mounted = []
            self.pending_leaf_ids = tuple(
                str(item).strip() for item in mounted[:12] if str(item).strip()
            )
            namespace = observation.get("namespace")
            namespace_id = namespace.get("id") if isinstance(namespace, dict) else None
            if namespace_id:
                self.pending_discovery_ids = (str(namespace_id),)
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
            self.pending_leaf_ids = ()
            self.pending_discovery_ids = ()
