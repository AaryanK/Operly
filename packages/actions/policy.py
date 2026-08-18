from dataclasses import dataclass
from enum import StrEnum


class PolicyDecisionType(StrEnum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    decision: PolicyDecisionType
    reason: str


def evaluate_action(action, tenant_context=None) -> PolicyDecision:
    del tenant_context
    capability = action.capability
    if capability == "read_analytics" or capability == "draft_content":
        return PolicyDecision(PolicyDecisionType.ALLOW, "Read-only or draft operation")
    if capability in {"publish_website", "publish_content", "send_bulk_messages", "delete_important_records", "update_website"}:
        return PolicyDecision(PolicyDecisionType.REQUIRE_APPROVAL, "Consequential public or record-changing operation")
    if capability == "spend_money": return PolicyDecision(PolicyDecisionType.DENY, "Autonomous spending is unsupported")
    return PolicyDecision(PolicyDecisionType.ALLOW if action.risk_level in {"read_only", "low"} else PolicyDecisionType.REQUIRE_APPROVAL,
                          "Decision derived from declared risk")
