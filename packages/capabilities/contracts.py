from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    risk_level: str = "low"
    reversible: bool = False


@dataclass(slots=True)
class CapabilityResult:
    success: bool
    changed: bool
    evidence: dict[str, Any] = field(default_factory=dict)
    external_reference: str | None = None


class CapabilityProvider(Protocol):
    name: str
    capabilities: tuple[CapabilityDefinition, ...]
    def supports(self, capability_name: str) -> bool: ...
    async def execute(self, context: Any, capability_name: str, arguments: dict[str, Any]) -> CapabilityResult: ...
    async def verify(self, context: Any, capability_name: str, arguments: dict[str, Any], result: CapabilityResult) -> CapabilityResult: ...
