from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from packages.capabilities.contracts import (
    ApprovalPolicy,
    CapabilityEffect,
    DataEgress,
)


class PolicyDecisionType(StrEnum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    decision: PolicyDecisionType
    reason: str


@dataclass(frozen=True, slots=True)
class ActionPolicyContext:
    """Trusted application policy facts for one proposed action."""

    authorized: bool = True
    target_allowed: bool = True
    data_egress_allowed: bool = True
    external_writes_allowed: bool = True
    destructive_actions_allowed: bool = True
    force_approval: bool = False

    @classmethod
    def coerce(cls, value: Any) -> "ActionPolicyContext":
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            return cls()
        return cls(
            authorized=bool(value.get("authorized", True)),
            target_allowed=bool(value.get("target_allowed", True)),
            data_egress_allowed=bool(value.get("data_egress_allowed", True)),
            external_writes_allowed=bool(value.get("external_writes_allowed", True)),
            destructive_actions_allowed=bool(value.get("destructive_actions_allowed", True)),
            force_approval=bool(value.get("force_approval", False)),
        )


def _legacy_decision(action) -> PolicyDecision:
    """Compatibility only until ActionService passes the trusted definition.

    Do not add new capability names here. New providers must use effect/egress metadata;
    this branch exists solely to preserve current production semantics during cutover.
    """

    capability = action.capability
    if capability in {
        "read_analytics",
        "analytics.query",
        "company.read_state",
        "company.search_events",
        "crm.search_leads",
        "website.inspect",
        "messaging.draft",
    }:
        return PolicyDecision(PolicyDecisionType.ALLOW, "Legacy read-only/draft policy")
    if capability in {
        "publish_website",
        "publish_content",
        "send_bulk_messages",
        "delete_important_records",
        "update_website",
        "website.edit",
        "messaging.send",
    }:
        return PolicyDecision(PolicyDecisionType.REQUIRE_APPROVAL, "Legacy consequential-action policy")
    if capability == "spend_money":
        return PolicyDecision(PolicyDecisionType.DENY, "Autonomous spending is unsupported")
    return PolicyDecision(
        PolicyDecisionType.ALLOW
        if action.risk_level in {"read_only", "low"}
        else PolicyDecisionType.REQUIRE_APPROVAL,
        "Legacy decision derived from declared risk",
    )


def _definition_effect(definition, action) -> CapabilityEffect:
    value = getattr(definition, "effective_effect", None)
    if isinstance(value, CapabilityEffect):
        return value
    raw = getattr(definition, "effect", CapabilityEffect.AUTO)
    try:
        parsed = raw if isinstance(raw, CapabilityEffect) else CapabilityEffect(str(raw))
    except ValueError:
        parsed = CapabilityEffect.AUTO
    if parsed is not CapabilityEffect.AUTO:
        return parsed
    risk = str(getattr(action, "risk_level", "low") or "low").lower()
    return (
        CapabilityEffect.READ
        if risk == "read_only"
        else CapabilityEffect.COMPUTE
        if risk == "low"
        else CapabilityEffect.WRITE
    )


def _definition_egress(definition, effect: CapabilityEffect) -> DataEgress:
    value = getattr(definition, "effective_data_egress", None)
    if isinstance(value, DataEgress):
        return value
    raw = getattr(definition, "data_egress", DataEgress.AUTO)
    try:
        parsed = raw if isinstance(raw, DataEgress) else DataEgress(str(raw))
    except ValueError:
        parsed = DataEgress.AUTO
    if parsed is not DataEgress.AUTO:
        return parsed
    if effect in {CapabilityEffect.EXTERNAL_WRITE, CapabilityEffect.DESTRUCTIVE} and bool(
        getattr(definition, "integration_provider", None)
    ):
        return DataEgress.EXTERNAL
    return DataEgress.NONE


def evaluate_action(action, tenant_context=None, *, definition=None) -> PolicyDecision:
    """Evaluate allowed effect/egress and approval separately from authorization."""

    # Current ActionService does not yet pass definitions. Preserve its exact policy
    # behavior until that boundary migrates; otherwise this refactor could change
    # approvals before the new metadata is actually authoritative.
    if definition is None:
        return _legacy_decision(action)

    policy = ActionPolicyContext.coerce(tenant_context)
    if not policy.authorized:
        return PolicyDecision(PolicyDecisionType.DENY, "Action is outside current authorized policy scope")
    if not policy.target_allowed:
        return PolicyDecision(PolicyDecisionType.DENY, "Action target is not allowed by policy")

    effect = _definition_effect(definition, action)
    egress = _definition_egress(definition, effect)
    approval_policy = getattr(definition, "approval_policy", ApprovalPolicy.POLICY)
    try:
        approval_policy = (
            approval_policy
            if isinstance(approval_policy, ApprovalPolicy)
            else ApprovalPolicy(str(approval_policy))
        )
    except ValueError:
        approval_policy = ApprovalPolicy.POLICY

    if egress is DataEgress.EXTERNAL and not policy.data_egress_allowed:
        return PolicyDecision(PolicyDecisionType.DENY, "External data egress is blocked by policy")
    if effect is CapabilityEffect.EXTERNAL_WRITE and not policy.external_writes_allowed:
        return PolicyDecision(PolicyDecisionType.DENY, "External writes are blocked by policy")
    if effect is CapabilityEffect.DESTRUCTIVE and not policy.destructive_actions_allowed:
        return PolicyDecision(PolicyDecisionType.DENY, "Destructive actions are blocked by policy")
    if approval_policy is ApprovalPolicy.ALWAYS or policy.force_approval:
        return PolicyDecision(PolicyDecisionType.REQUIRE_APPROVAL, "Capability or workspace policy requires approval")
    if approval_policy is ApprovalPolicy.AUTO:
        return PolicyDecision(PolicyDecisionType.ALLOW, "Capability contract permits automatic execution")

    risk = str(getattr(action, "risk_level", "low") or "low").lower()
    reversible = bool(getattr(definition, "reversible", False))
    if effect in {CapabilityEffect.READ, CapabilityEffect.COMPUTE} and egress is DataEgress.NONE:
        if risk in {"read_only", "low"}:
            return PolicyDecision(PolicyDecisionType.ALLOW, "Read/compute operation has no external side effect")
    if effect is CapabilityEffect.WRITE:
        if risk == "low" and reversible:
            return PolicyDecision(PolicyDecisionType.ALLOW, "Low-risk reversible scoped write")
        return PolicyDecision(PolicyDecisionType.REQUIRE_APPROVAL, "Scoped write requires approval")
    if effect is CapabilityEffect.EXTERNAL_WRITE:
        return PolicyDecision(PolicyDecisionType.REQUIRE_APPROVAL, "External side effect requires approval")
    if effect is CapabilityEffect.DESTRUCTIVE:
        return PolicyDecision(PolicyDecisionType.REQUIRE_APPROVAL, "Destructive side effect requires approval")
    if egress is DataEgress.EXTERNAL:
        return PolicyDecision(PolicyDecisionType.REQUIRE_APPROVAL, "External data egress requires approval")
    return PolicyDecision(
        PolicyDecisionType.ALLOW if risk in {"read_only", "low"} else PolicyDecisionType.REQUIRE_APPROVAL,
        "Decision derived from declared effect, egress and risk",
    )
