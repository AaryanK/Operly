"""ActionService with first-class initiator/executor provenance.

The legacy ActionService owns the mature proposal/approval/execution/verification
state machine. This subclass changes only event attribution so we keep one action
lifecycle while making human-vs-agent provenance explicit for audit and workflows.
"""
from __future__ import annotations

from typing import Any

from packages.actions.service import ActionService
from packages.company.events import append_event


class AttributedActionService(ActionService):
    def __init__(
        self,
        *args,
        initiator_type: str | None = None,
        initiator_id: str | None = None,
        executor_type: str | None = None,
        executor_id: str | None = None,
        delegation_chain: list[dict[str, Any]] | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.initiator_type = str(initiator_type or "system")
        self.initiator_id = str(initiator_id or "").strip() or None
        self.executor_type = str(executor_type or self.initiator_type or "system")
        self.executor_id = str(executor_id or self.initiator_id or "").strip() or None
        self.delegation_chain = [
            dict(item)
            for item in (delegation_chain or [])
            if isinstance(item, dict)
        ]

    async def _event(self, action, event_type, payload=None):
        # Every action is traceable, including Personal actions. Personal events stay
        # personal-scoped and therefore never wake workspace workflows accidentally.
        provenance = {
            key: value
            for key, value in {
                "principal_id": action.principal_id,
                "client_id": action.client_id,
                "origin": action.origin,
                "connector_id": action.connector_id,
                "resource_type": action.resource_type,
                "scope_kind": action.scope_kind,
            }.items()
            if value
        }
        event = await append_event(
            self.db,
            tenant_id=action.tenant_id,
            owner_user_id=action.owner_user_id,
            event_type=event_type,
            payload={
                "action_id": action.id,
                "capability": action.capability,
                "status": action.status,
                **provenance,
                **(payload or {}),
            },
            actor_type=self.executor_type,
            actor_id=self.executor_id,
            initiator_type=self.initiator_type,
            initiator_id=self.initiator_id,
            executor_type=self.executor_type,
            executor_id=self.executor_id,
            delegation_chain=self.delegation_chain,
            correlation_id=action.correlation_id,
            causation_id=action.id,
            source="actions",
        )
        normalized = {
            ("messaging.send", "action.proposed"): "message.send_requested",
            ("messaging.send", "action.approved"): "message.send_approved",
            ("messaging.send", "action.failed"): "message.send_failed",
            ("calendar.create_event", "action.failed"): "calendar.event_failed",
            ("solution.apply_improvement", "action.rejected"): "solution.change.rejected",
        }.get((action.capability, event_type))
        if normalized:
            await append_event(
                self.db,
                tenant_id=action.tenant_id,
                owner_user_id=action.owner_user_id,
                event_type=normalized,
                payload={"action_id": action.id, **provenance, **(payload or {})},
                actor_type=self.executor_type,
                actor_id=self.executor_id,
                initiator_type=self.initiator_type,
                initiator_id=self.initiator_id,
                executor_type=self.executor_type,
                executor_id=self.executor_id,
                delegation_chain=self.delegation_chain,
                correlation_id=action.correlation_id,
                causation_id=action.id,
                source="actions",
            )
        return event
