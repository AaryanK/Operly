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
    """Universal capability specification.

    ``name`` and ``risk_level`` are retained as compatibility field names for the
    existing tool loop. New code should treat ``id`` as the stable capability id
    and may use plugin/tags/semantic_operations for discovery and composition.
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
    source: str = "operly_builtin"
    provider: str = "operly"
    integration_provider: str | None = None
    credential_scopes: tuple[str, ...] = ()
    reversible: bool = False
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
