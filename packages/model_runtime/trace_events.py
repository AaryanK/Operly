"""Stable event names for end-to-end runtime observability."""
from __future__ import annotations

from enum import StrEnum


class RuntimeTraceEvent(StrEnum):
    ROUTE_SELECTED = "route.selected"
    MODEL_REQUEST = "model.request"
    MODEL_RESPONSE = "model.response"
    MODEL_ESCALATED = "model.escalated"
    CAPABILITY_REQUESTED = "capability.requested"
    CAPABILITY_REJECTED = "capability.rejected"
    CAPABILITY_SEARCHED = "capability.searched"
    CAPABILITY_EXPOSED = "capability.exposed"
    CONTEXT_SEARCHED = "context.searched"
    CONTEXT_MATERIALIZED = "context.materialized"
    RUN_COMPACTED = "run.compacted"
    PLAN_CREATED = "plan.created"
    PLAN_REVISED = "plan.revised"
    ACTION_CREATED = "action.created"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_RESOLVED = "approval.resolved"
    ACTION_RESUMED = "action.resumed"
    CONNECTOR_REQUEST = "connector.request"
    CONNECTOR_RESPONSE = "connector.response"
    DELIVERY_VERIFIED = "delivery.verified"
    DELIVERY_FAILED = "delivery.failed"
    WORKFLOW_COMPLETED = "workflow.completed"


TRACE_EVENT_VALUES = frozenset(item.value for item in RuntimeTraceEvent)
