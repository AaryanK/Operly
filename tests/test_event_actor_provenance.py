import unittest
from datetime import datetime

from packages.company.events.service import BusinessEvent
from packages.tasks.events import event_context, event_matches


class EventActorProvenanceTests(unittest.TestCase):
    def _event(self):
        return BusinessEvent(
            id="evt-1",
            tenant_id="workspace-1",
            event_type="crm.customer.created",
            occurred_at=datetime(2026, 8, 26, 12, 0, 0),
            actor_type="agent",
            actor_id="operly:business_agent",
            source="actions",
            payload={"customer_id": "customer-1"},
            correlation_id="trace-1",
            causation_id="action-1",
            metadata={},
            initiator_type="user",
            initiator_id="user:raju",
            executor_type="agent",
            executor_id="operly:business_agent",
            delegation_chain=(
                {
                    "from": "user:raju",
                    "to": "operly:business_agent",
                    "kind": "requested_action",
                },
            ),
        )

    def test_workflow_can_trigger_on_human_initiator(self):
        event = self._event()
        self.assertTrue(
            event_matches(
                {
                    "kind": "event",
                    "event_id": "crm.customer.created",
                    "where": {"initiator_id": "user:raju"},
                },
                event,
            )
        )
        self.assertFalse(
            event_matches(
                {
                    "kind": "event",
                    "event_id": "crm.customer.created",
                    "where": {"initiator_id": "operly:business_agent"},
                },
                event,
            )
        )

    def test_workflow_can_trigger_on_executor_independently(self):
        event = self._event()
        self.assertTrue(
            event_matches(
                {
                    "kind": "event",
                    "event_id": "crm.customer.created",
                    "where": {
                        "executor_type": "agent",
                        "executor_id": "operly:business_agent",
                    },
                },
                event,
            )
        )

    def test_event_context_preserves_actor_chain(self):
        context = event_context(self._event())
        self.assertEqual(context["initiator_id"], "user:raju")
        self.assertEqual(context["executor_id"], "operly:business_agent")
        self.assertEqual(context["actor_id"], "operly:business_agent")
        self.assertEqual(context["delegation_chain"][0]["from"], "user:raju")


if __name__ == "__main__":
    unittest.main()
