from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class ApprovalPolicy(StrEnum):
    AUTO = "auto"
    POLICY = "policy"
    ALWAYS = "always"


class ExecutionMode(StrEnum):
    CONTROL_PLANE = "control_plane"
    EXTERNAL = "external"
    ISOLATED_RUNNER = "isolated_runner"


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    id: str
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    version: str = "1.0.0"
    risk_level: str = "low"
    permissions: tuple[str, ...] = ()
    approval_policy: ApprovalPolicy = ApprovalPolicy.POLICY
    execution_mode: ExecutionMode = ExecutionMode.CONTROL_PLANE
    source: str = "operly_builtin"
    provider: str = "operly"
    integration_provider: str | None = None
    credential_scopes: tuple[str, ...] = ()
    reversible: bool = False

    def model_tool_schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.id, "description": self.description,
                "parameters": self.input_schema}}


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
    # Providers may additionally implement compensate(...) and health_check(...).


# Plugin is the universal public name. Capability aliases remain for existing callers.
PluginDefinition = CapabilityDefinition
PluginProvider = CapabilityProvider
PluginResult = CapabilityResult
