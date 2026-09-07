from __future__ import annotations

import json
import unittest

from packages.agent_runtime.context import ContextSlice
from packages.agent_runtime.interactive import resolve_runtime_dispatch
from packages.agent_runtime.objective import (
    ObjectiveComplexity,
    ObjectiveIR,
    ObjectiveInterpreterRequest,
    ObjectiveKind,
    ObjectiveOperation,
    RuntimeDispatchPath,
)
from packages.kernel.contracts import CapabilityRisk, CapabilitySpec
from packages.kernel.runtime_availability import _focus_resource_family


def capability(
    capability_id: str,
    name: str,
    description: str,
    *,
    required: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
) -> CapabilitySpec:
    return CapabilitySpec(
        id=capability_id,
        version="1.0.0",
        display_name=name,
        description=description,
        provider_id="test",
        scopes=frozenset({"personal"}),
        input_schema={
            "type": "object",
            "properties": {field: {"type": "string"} for field in required},
            "required": list(required),
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        risk=CapabilityRisk.READ_ONLY,
        tags=frozenset(tags),
    )


class RuntimeResourceResolutionTests(unittest.TestCase):
    def test_resource_focus_drops_unrelated_read_tools_but_keeps_family_sibling(self):
        gmail_read = capability(
            "google.gmail.read_message",
            "Read personal Gmail message",
            "Read one Gmail message by provider message ID.",
            required=("message_id",),
            tags=("gmail", "mail", "read"),
        )
        gmail_search = capability(
            "google.gmail.search",
            "Search personal Gmail",
            "Search an account-owned Gmail mailbox.",
            required=("query",),
            tags=("gmail", "mail", "search", "read"),
        )
        calendar = capability(
            "google.calendar.list_events",
            "List personal Google Calendar events",
            "List calendar events in a time window.",
            required=("time_min", "time_max"),
            tags=("calendar", "events", "read"),
        )
        workflow = capability(
            "workflow.trace",
            "Read workflow trace",
            "Read a workflow execution trace.",
            tags=("workflow", "trace", "read"),
        )

        focused = _focus_resource_family(
            "Read the most recent email from dad | resources email message | operations retrieve",
            (gmail_read, calendar, gmail_search, workflow),
        )
        self.assertEqual(
            [spec.id for spec in focused],
            ["google.gmail.read_message", "google.gmail.search"],
        )

    def test_direct_retrieval_is_promoted_when_search_must_resolve_opaque_id(self):
        objective = ObjectiveIR(
            objective="Read the most recent email from dad",
            kind=ObjectiveKind.RETRIEVE,
            operations=(ObjectiveOperation.RETRIEVE,),
            resource_hints=("email", "message"),
            requires_external_state=True,
            requires_mutation=False,
            requires_future_wait=False,
            complexity=ObjectiveComplexity.SIMPLE,
        )
        gmail_read = capability(
            "google.gmail.read_message",
            "Read personal Gmail message",
            "Read one Gmail message by provider message ID.",
            required=("message_id",),
            tags=("gmail", "mail", "read"),
        )
        gmail_search = capability(
            "google.gmail.search",
            "Search personal Gmail",
            "Search an account-owned Gmail mailbox.",
            required=("query",),
            tags=("gmail", "mail", "search", "read"),
        )

        self.assertEqual(objective.dispatch_path(), RuntimeDispatchPath.DIRECT_CAPABILITY)
        self.assertEqual(
            resolve_runtime_dispatch(objective, (gmail_read, gmail_search)),
            RuntimeDispatchPath.AGENT_LOOP,
        )

    def test_direct_retrieval_stays_direct_without_resolution_dependency(self):
        objective = ObjectiveIR(
            objective="List calendar events tomorrow",
            kind=ObjectiveKind.RETRIEVE,
            operations=(ObjectiveOperation.RETRIEVE,),
            resource_hints=("calendar", "event"),
            requires_external_state=True,
            requires_mutation=False,
            requires_future_wait=False,
            complexity=ObjectiveComplexity.SIMPLE,
        )
        list_events = capability(
            "google.calendar.list_events",
            "List personal Google Calendar events",
            "List events within an explicit time window.",
            required=("time_min", "time_max"),
            tags=("calendar", "events", "list", "read"),
        )
        self.assertEqual(
            resolve_runtime_dispatch(objective, (list_events,)),
            RuntimeDispatchPath.DIRECT_CAPABILITY,
        )

    def test_objective_user_payload_does_not_duplicate_system_instructions(self):
        request = ObjectiveInterpreterRequest(
            message="what did dad email me last",
            scope_kind="personal",
            surface="discord_dm",
            relevant_context=ContextSlice(items=(), total_bytes=0, omitted_count=0),
        )
        payload = request.as_dict()
        self.assertNotIn("instructions", payload)
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        self.assertLess(len(encoded), 2_000)


if __name__ == "__main__":
    unittest.main()
