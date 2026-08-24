"""Progressive capability exposure for one model session."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from packages.model_runtime.trace_context import current_trace_metadata


DEFAULT_KERNEL_IDS = frozenset(
    {
        "capability.search",
        "capability.describe",
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
    registry: Any
    tenant_id: str
    authority: set[str]
    visible_predicate: Callable[[str], bool] | None = None
    initial_ids: Iterable[str] = ()
    exposed_ids: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.exposed_ids.update(DEFAULT_KERNEL_IDS)
        self.exposed_ids.update(str(item) for item in self.initial_ids if str(item))
        self.expose_seamless_defaults()

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
    def _seamless_default(definition) -> bool:
        """Return whether an authorized capability should be visible immediately.

        Normal observations and low-risk operations should not depend on a model
        remembering a search/describe ceremony. Connected integrations are also
        surfaced directly after their provider/scope gate succeeds. Medium/high-risk
        or uncommon capabilities remain progressively discoverable, and every call
        still crosses the canonical firewall/approval boundary.
        """
        if definition.risk_level in {"read_only", "low"}:
            return True
        if definition.integration_provider:
            return True
        return False

    @staticmethod
    def _stage_allows(definition, stage: str) -> bool:
        """Reduce model-visible authority without ever granting new authority.

        ``adaptive`` preserves current behavior. Durable workflows can set
        ``capability_stage`` in runtime trace metadata to constrain each model turn.
        The firewall remains authoritative even if a caller supplies a bad stage.
        """
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

    def expose_seamless_defaults(self) -> None:
        for definition in self.registry.definitions():
            if self._seamless_default(definition) and self._visible(definition.id):
                self.exposed_ids.add(definition.id)

    def schemas(self, *, stage: str | None = None) -> list[dict[str, Any]]:
        # Registry/connector availability can change during a conversation. Refresh
        # the seamless set every turn while _visible() removes anything revoked.
        self.expose_seamless_defaults()
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
        """Expand exact schemas after capability.describe.

        Search/describe remains useful for uncommon medium/high-risk capabilities,
        but ordinary reads, low-risk operations, and connector tools are already
        available without spending model turns on discovery.
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
