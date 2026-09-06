from __future__ import annotations

import unittest

from packages.agent_runtime.objective import (
    ObjectiveComplexity,
    ObjectiveIR,
    ObjectiveKind,
    ObjectiveOperation,
    RuntimeDispatchPath,
)
from packages.personal_modules.runtime import build_personal_runtime
from packages.security.execution_context import (
    ExecutionContext,
    PERSONAL_EXECUTION_PERMISSIONS,
    ScopeKind,
)
from packages.security.surfaces import SurfaceKind


def personal_context() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=None,
        user_id="user-1",
        membership_id=None,
        role="personal_owner",
        permissions=PERSONAL_EXECUTION_PERMISSIONS,
        channel="web",
        surface=SurfaceKind.PERSONAL_PRIVATE,
        conversation_id="conversation-1",
        scope_kind=ScopeKind.PERSONAL,
        principal_id="user:user-1",
        workspace_mode="personal",
    )


class Runtime1RetrievalHealthTests(unittest.TestCase):
    """Broad semantic retrieval scorecard over the real Personal capability registry.

    These tests intentionally use natural user wording as well as the compact ObjectiveIR
    query shape. They are not a substitute for evaluating the live inference model, but
    they protect the deterministic classifier -> capability-retrieval boundary that must
    surface the right authorized tools after semantic interpretation.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = build_personal_runtime().registry
        cls.context = personal_context()

    def _ids(self, query: str, *, limit: int = 12) -> list[str]:
        return [
            spec.id
            for spec in self.registry.search(
                query,
                context=self.context,
                effective_only=True,
                limit=limit,
            )
        ]

    def _assert_near_top(self, query: str, capability_id: str, *, top: int = 5) -> None:
        ids = self._ids(query)
        self.assertIn(capability_id, ids, msg=f"{capability_id!r} missing for {query!r}; got {ids}")
        self.assertLess(
            ids.index(capability_id),
            top,
            msg=f"{capability_id!r} ranked too low for {query!r}; got {ids}",
        )

    def test_raw_user_wording_retrieval_matrix(self):
        cases = (
            ("search my emails for dad's emails", "google.gmail.search", 4),
            ("find emails from Dad", "google.gmail.search", 4),
            ("look through my inbox for the tuition receipt", "google.gmail.search", 5),
            ("search Gmail for the workshop email", "google.gmail.search", 4),
            ("read a Gmail message by its message id", "google.gmail.read_message", 4),
            ("show me what is on my calendar tomorrow", "google.calendar.list_events", 5),
            ("list my meetings for this week", "google.calendar.list_events", 5),
            ("what events are on my schedule today", "google.calendar.list_events", 5),
            ("am I free Friday afternoon", "google.calendar.freebusy", 5),
            ("check my calendar availability tomorrow at 3", "google.calendar.freebusy", 5),
            ("list my Google calendars", "google.calendar.list_calendars", 4),
            ("show my open tasks", "tasks.list", 4),
            ("what tasks do I have", "tasks.list", 4),
            ("list my workflows", "workflow.list", 5),
            ("show recent workflow runs", "workflow.run.list", 5),
            ("inspect this workflow run", "workflow.run.get", 5),
            ("read the workflow trace", "workflow.trace", 5),
            ("show me the versions of this workflow", "workflow.version.list", 5),
            ("preview the next workflow schedule occurrences", "workflow.schedule.preview", 5),
            ("check workflow runtime health", "workflow.runtime.status", 5),
            ("check Operly system runtime status", "system.runtime.status", 5),
        )
        for query, capability_id, top in cases:
            with self.subTest(query=query, capability_id=capability_id):
                self._assert_near_top(query, capability_id, top=top)

    def test_raw_user_wording_action_matrix(self):
        cases = (
            ("send an email to Dad", "google.gmail.send_email", 4),
            ("draft an email to Dad", "google.gmail.create_draft", 4),
            ("create a calendar event tomorrow", "google.calendar.create_event", 4),
            ("update my calendar event", "google.calendar.update_event", 4),
            ("delete that calendar event", "google.calendar.delete_event", 4),
            ("create a task called submit report", "tasks.create", 4),
            ("mark my task as done", "tasks.update_status", 5),
            ("create a workflow", "workflow.create", 4),
            ("run this workflow now", "workflow.run.start", 5),
            ("disable this workflow", "workflow.disable", 5),
            ("enable this workflow", "workflow.enable", 5),
            ("cancel this workflow run", "workflow.run.cancel", 5),
            ("retry the failed workflow run", "workflow.run.retry", 5),
        )
        for query, capability_id, top in cases:
            with self.subTest(query=query, capability_id=capability_id):
                self._assert_near_top(query, capability_id, top=top)

    def test_compact_classifier_output_retrieval_matrix(self):
        cases = (
            (
                "Find Dad's emails",
                ObjectiveKind.RETRIEVE,
                (ObjectiveOperation.RETRIEVE,),
                ("emails",),
                "google.gmail.search",
                4,
            ),
            (
                "Read a selected email message",
                ObjectiveKind.RETRIEVE,
                (ObjectiveOperation.RETRIEVE,),
                ("email message",),
                "google.gmail.read_message",
                5,
            ),
            (
                "List meetings tomorrow",
                ObjectiveKind.RETRIEVE,
                (ObjectiveOperation.RETRIEVE,),
                ("calendar events",),
                "google.calendar.list_events",
                5,
            ),
            (
                "Check whether the user is free",
                ObjectiveKind.RETRIEVE,
                (ObjectiveOperation.RETRIEVE,),
                ("calendar availability",),
                "google.calendar.freebusy",
                5,
            ),
            (
                "List the user's calendars",
                ObjectiveKind.RETRIEVE,
                (ObjectiveOperation.RETRIEVE,),
                ("calendars",),
                "google.calendar.list_calendars",
                5,
            ),
            (
                "List personal tasks",
                ObjectiveKind.RETRIEVE,
                (ObjectiveOperation.RETRIEVE,),
                ("tasks",),
                "tasks.list",
                4,
            ),
            (
                "List workflows",
                ObjectiveKind.RETRIEVE,
                (ObjectiveOperation.RETRIEVE,),
                ("workflows",),
                "workflow.list",
                5,
            ),
            (
                "Inspect a workflow run",
                ObjectiveKind.RETRIEVE,
                (ObjectiveOperation.RETRIEVE,),
                ("workflow run",),
                "workflow.run.get",
                5,
            ),
            (
                "Read workflow execution trace",
                ObjectiveKind.RETRIEVE,
                (ObjectiveOperation.RETRIEVE,),
                ("workflow trace",),
                "workflow.trace",
                5,
            ),
        )
        for objective, kind, operations, resources, capability_id, top in cases:
            with self.subTest(objective=objective, capability_id=capability_id):
                ir = ObjectiveIR(
                    objective=objective,
                    kind=kind,
                    operations=operations,
                    resource_hints=resources,
                    requires_external_state=True,
                    requires_mutation=False,
                    requires_future_wait=False,
                    complexity=ObjectiveComplexity.SIMPLE,
                )
                self.assertEqual(ir.dispatch_path(), RuntimeDispatchPath.DIRECT_CAPABILITY)
                self._assert_near_top(ir.capability_query(), capability_id, top=top)

    def test_compact_classifier_output_action_matrix(self):
        cases = (
            ("Send Dad an email", ("emails",), "google.gmail.send_email", 4),
            ("Draft an email for Dad", ("emails",), "google.gmail.create_draft", 4),
            ("Create a calendar meeting", ("calendar events",), "google.calendar.create_event", 4),
            ("Create a personal task", ("tasks",), "tasks.create", 4),
            ("Create a workflow", ("workflows",), "workflow.create", 4),
            ("Run the selected workflow now", ("workflow",), "workflow.run.start", 5),
        )
        for objective, resources, capability_id, top in cases:
            with self.subTest(objective=objective, capability_id=capability_id):
                ir = ObjectiveIR(
                    objective=objective,
                    kind=ObjectiveKind.ACT,
                    operations=(ObjectiveOperation.ACT,),
                    resource_hints=resources,
                    requires_external_state=True,
                    requires_mutation=True,
                    requires_future_wait=False,
                    complexity=ObjectiveComplexity.SIMPLE,
                )
                self.assertEqual(ir.dispatch_path(), RuntimeDispatchPath.DIRECT_CAPABILITY)
                self._assert_near_top(ir.capability_query(), capability_id, top=top)

    def test_retrieval_queries_prefer_reads_over_same_domain_mutations(self):
        cases = (
            (
                "Find my email from Dad | resources emails | operations retrieve",
                "google.gmail.search",
                ("google.gmail.send_email", "google.gmail.create_draft", "google.gmail.modify_labels"),
            ),
            (
                "Show tomorrow's calendar | resources calendar events | operations retrieve",
                "google.calendar.list_events",
                ("google.calendar.create_event", "google.calendar.update_event", "google.calendar.delete_event"),
            ),
            (
                "List workflows | resources workflows | operations retrieve",
                "workflow.list",
                ("workflow.create", "workflow.update", "workflow.archive"),
            ),
        )
        for query, read_capability, mutations in cases:
            with self.subTest(query=query):
                ids = self._ids(query)
                self.assertIn(read_capability, ids)
                read_index = ids.index(read_capability)
                for mutation in mutations:
                    if mutation in ids:
                        self.assertLess(read_index, ids.index(mutation), msg=f"{query!r}: {ids}")


if __name__ == "__main__":
    unittest.main()
