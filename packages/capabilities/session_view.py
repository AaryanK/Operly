"""Progressive capability exposure for one model session."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from packages.capabilities.contracts import ApprovalPolicy


DEFAULT_KERNEL_IDS = frozenset(
    {
        "capability.search",
        "capability.describe",
        "model.invoke",
    }
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

        Read operations should never require a search/describe round trip. Connected
        integrations are also surfaced directly because their provider and OAuth
        scope checks have already happened before this view is built. Low-risk AUTO
        operations are cheap enough to expose while the firewall remains the final
        execution/approval authority.
        """
        if definition.risk_level == "read_only":
            return True
        if definition.integration_provider:
            return True
        return (
            definition.risk_level == "low"
            and definition.approval_policy == ApprovalPolicy.AUTO
        )

    def expose_seamless_defaults(self) -> None:
        for definition in self.registry.definitions():
            if self._seamless_default(definition) and self._visible(definition.id):
                self.exposed_ids.add(definition.id)

    def schemas(self) -> list[dict[str, Any]]:
        schemas = []
        for capability_id in sorted(self.exposed_ids):
            if not self._visible(capability_id):
                continue
            definition = self.registry.definition(capability_id)
            schemas.append(definition.model_tool_schema())
        return schemas

    def expose(self, capability_ids: Iterable[str]) -> None:
        for capability_id in capability_ids:
            clean = str(capability_id or "").strip()
            if clean and self._visible(clean):
                self.exposed_ids.add(clean)

    def observe(self, capability_id: str, invocation_result: dict[str, Any]) -> None:
        """Expand exact schemas after capability.describe.

        Search/describe remains useful for uncommon capabilities, but ordinary read,
        connector, and low-risk AUTO tools are already available without spending
        model turns on discovery.
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
