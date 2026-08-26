from types import SimpleNamespace

from packages.actions.policy import PolicyDecisionType, evaluate_action
from packages.capabilities.contracts import (
    ApprovalPolicy,
    CapabilityDefinition,
    CapabilityEffect,
    DataEgress,
)


def _action(capability="demo", risk="low"):
    return SimpleNamespace(capability=capability, risk_level=risk)


def _definition(**overrides):
    values = {
        "id": "demo.action",
        "name": "demo_action",
        "description": "demo",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
    }
    values.update(overrides)
    return CapabilityDefinition(**values)


def test_legacy_policy_behavior_is_preserved_until_action_service_cutover():
    assert evaluate_action(_action("messaging.send")).decision is PolicyDecisionType.REQUIRE_APPROVAL
    assert evaluate_action(_action("analytics.query", "read_only")).decision is PolicyDecisionType.ALLOW
    assert evaluate_action(_action("spend_money", "high")).decision is PolicyDecisionType.DENY


def test_effect_policy_distinguishes_read_from_external_write():
    read = _definition(
        risk_level="read_only",
        effect=CapabilityEffect.READ,
        approval_policy=ApprovalPolicy.POLICY,
    )
    external = _definition(
        risk_level="low",
        effect=CapabilityEffect.EXTERNAL_WRITE,
        data_egress=DataEgress.EXTERNAL,
        integration_provider="email",
        approval_policy=ApprovalPolicy.POLICY,
    )
    assert evaluate_action(_action(risk="read_only"), definition=read).decision is PolicyDecisionType.ALLOW
    assert evaluate_action(_action(), definition=external).decision is PolicyDecisionType.REQUIRE_APPROVAL


def test_effect_policy_can_hard_deny_external_egress():
    definition = _definition(
        effect=CapabilityEffect.EXTERNAL_WRITE,
        data_egress=DataEgress.EXTERNAL,
        integration_provider="email",
    )
    decision = evaluate_action(
        _action(),
        {"data_egress_allowed": False},
        definition=definition,
    )
    assert decision.decision is PolicyDecisionType.DENY


def test_explicit_auto_still_cannot_override_hard_workspace_policy_deny():
    definition = _definition(
        effect=CapabilityEffect.EXTERNAL_WRITE,
        data_egress=DataEgress.EXTERNAL,
        integration_provider="slack",
        approval_policy=ApprovalPolicy.AUTO,
    )
    decision = evaluate_action(
        _action(),
        {"external_writes_allowed": False},
        definition=definition,
    )
    assert decision.decision is PolicyDecisionType.DENY
