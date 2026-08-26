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


class CapabilityEffect(StrEnum):
    """Declared effect class used by the policy engine before execution."""

    AUTO = "auto"
    READ = "read"
    COMPUTE = "compute"
    WRITE = "write"
    EXTERNAL_WRITE = "external_write"
    DESTRUCTIVE = "destructive"


class DataEgress(StrEnum):
    """Whether invoking a capability can move scoped data across a trust boundary."""

    AUTO = "auto"
    NONE = "none"
    SAME_SCOPE = "same_scope"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    """Universal capability specification.

    ``name`` and ``risk_level`` are retained as compatibility field names for the
    existing tool loop. New code should treat ``id`` as the stable capability id
    and may use plugin/tags/semantic_operations for discovery and composition.
    ``execution_timeout_seconds`` is an application-enforced upper bound for one
    provider invocation; the firewall clamps it again before execution.

    ``effect`` and ``data_egress`` are policy metadata, not authority. Existing
    capabilities may leave them AUTO while they migrate; policy derives a conservative
    class from risk/execution/integration metadata. New consequential capabilities
    should declare them explicitly.
    """

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
    execution_timeout_seconds: int = 30
    source: str = "operly_builtin"
    provider: str = "operly"
    integration_provider: str | None = None
    credential_scopes: tuple[str, ...] = ()
    reversible: bool = False
    effect: CapabilityEffect = CapabilityEffect.AUTO
    data_egress: DataEgress = DataEgress.AUTO
    category: str | None = None
    display_name: str | None = None
    event_capabilities: tuple[str, ...] = ()
    health_check: dict[str, Any] | None = None
    allowed_network_domains: tuple[str, ...] = ()
    configuration_schema: dict[str, Any] | None = None
    plugin_id: str = "core"
    tags: frozenset[str] = frozenset()
    semantic_operations: frozenset[str] = frozenset()

    @property
    def risk(self) -> str:
        return self.risk_level

    @property
    def effective_effect(self) -> CapabilityEffect:
        if self.effect is not CapabilityEffect.AUTO:
            return self.effect
        risk = str(self.risk_level or "").strip().lower()
        if risk == "read_only":
            return CapabilityEffect.READ
        if self.execution_mode is ExecutionMode.ISOLATED_RUNNER and not self.integration_provider:
            return CapabilityEffect.COMPUTE
        # AUTO is intentionally not guessed as EXTERNAL_WRITE from the presence of an
        # integration alone because many external providers are read-only. Medium/high
        # risk remains a write-class operation until the provider declares more detail.
        if risk in {"medium", "high", "critical"}:
            return CapabilityEffect.WRITE
        return CapabilityEffect.COMPUTE

    @property
    def effective_data_egress(self) -> DataEgress:
        if self.data_egress is not DataEgress.AUTO:
            return self.data_egress
        if self.effective_effect in {CapabilityEffect.EXTERNAL_WRITE, CapabilityEffect.DESTRUCTIVE}:
            return DataEgress.EXTERNAL if self.integration_provider else DataEgress.SAME_SCOPE
        return DataEgress.NONE

    def model_tool_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    def discovery_document(self) -> str:
        pieces = [
            self.id,
            self.display_name or self.name,
            self.description,
            self.category or "",
            " ".join(sorted(self.tags)),
            " ".join(sorted(self.semantic_operations)),
        ]
        return " ".join(piece for piece in pieces if piece).lower()


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    id: str
    version: str
    plugin_id: str
    display_name: str
    description: str
    risk: str
    execution_mode: str
    permissions: tuple[str, ...]
    category: str | None
    tags: tuple[str, ...]
    semantic_operations: tuple[str, ...]
    installed: bool = True
    configured: bool = True
    healthy: bool | None = None
    authorized: bool | None = None


@dataclass(frozen=True, slots=True)
class CapabilityAvailability:
    """Machine-readable explanation of whether a capability can be used now."""

    available: bool
    configured: bool
    healthy: bool | None
    missing_scopes: tuple[str, ...] = ()
    missing_connector: str | None = None
    permission_denied: bool = False
    retryable: bool = False
    next_action: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "configured": self.configured,
            "healthy": self.healthy,
            "missingScopes": list(self.missing_scopes),
            "missingConnector": self.missing_connector,
            "permissionDenied": self.permission_denied,
            "retryable": self.retryable,
            "nextAction": self.next_action,
            "reason": self.reason,
        }


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

    async def execute(
        self,
        context: Any,
        capability_name: str,
        arguments: dict[str, Any],
    ) -> CapabilityResult: ...

    async def verify(
        self,
        context: Any,
        capability_name: str,
        arguments: dict[str, Any],
        result: CapabilityResult,
    ) -> CapabilityResult: ...
    # Providers may additionally implement compensate(...) and health_check(...).


# Target architecture terminology. Existing callers may keep the old aliases while
# migration proceeds; they resolve to one contract rather than a second tool model.
CapabilitySpec = CapabilityDefinition
PluginDefinition = CapabilityDefinition
PluginProvider = CapabilityProvider
PluginResult = CapabilityResult
