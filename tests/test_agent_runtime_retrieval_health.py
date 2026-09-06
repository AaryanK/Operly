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
    """Retrieval health over the real Personal capability registry.

    Production quality is measured at the semantic ObjectiveIR -> capability boundary.
    Raw-text registry search remains a compatibility fallback, so its contract is only
    bounded candidate recall for straightforward wording rather than language
    understanding or semantic paraphrase coverage. The live objective evaluator owns
    slang, typos, multilingual input, code switching and semantic paraphrases because
    that is intentionally model work, not Kernel keyword work.
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

    def _assert_raw_fallback_recall(self, query: str, capability_id: str) -> None:
        ids = self._ids(query)
        self.assertIn(
            capability_id,
            ids,
            msg=(
                f"raw compatibility search lost {capability_id!r} entirely for {query!r}; "
                f"got {ids}"
            ),
        )

    def test_raw_user_wording_retrieval_fallback_recall(self):
        cases = (
            ("search my emails for dad's emails", "google.gmail.search"),
            ("find emails from Dad", "google.gmail.search"),
            ("look through my inbox for the tuition receipt", "google.gmail.search"),
            ("search Gmail for the workshop email", "google.gmail.search"),
            ("read a Gmail message by its message id", "google.gmail.read_message"),
            ("show me what is on my calendar tomorrow", "google.calendar.list_events"),
            ("list my meetings for this week", "google.calendar.list_events"),
            ("what events are on my schedule today", "google.calendar.list_events"),
            ("am I free Friday afternoon", "google.calendar.freebusy"),
            ("check my calendar availability tomorrow at 3", "google.calendar.freebusy"),
            ("list my Google calendars", "google.calendar.list_calendars"),
            ("show my open tasks", "tasks.list"),
            ("what tasks do I have", "tasks.list"),
            ("list my workflows", "workflow.list"),
            ("show recent workflow runs", "workflow.run.list"),
            ("inspect this workflow run", "workflow.run.get"),
            ("read the workflow trace", "workflow.trace"),
            ("preview the next workflow schedule occurrences", "workflow.schedule.preview"),
            ("check workflow runtime health", "workflow.runtime.status"),
            ("check Operly system runtime status", "system.runtime.status"),
        )
        for query, capability_id in cases:
            with self.subTest(query=query, capability_id=capability_id):
                self._assert_raw_fallback_recall(query, capability_id)

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
                "Search Dad's mail messages",
                ObjectiveKind.RETRIEVE,
                (ObjectiveOperation.RETRIEVE,),
                ("email", "message"),
                "google.gmail.search",
                4,
            ),
            (
                "Read a selected mail message",
                ObjectiveKind.RETRIEVE,
                (ObjectiveOperation.RETRIEVE,),
                ("email", "message"),
                "google.gmail.read_message",
                5,
            ),
            (
                "List calendar events tomorrow",
                ObjectiveKind.RETRIEVE,
                (ObjectiveOperation.RETRIEVE,),
                ("calendar", "event"),
                "google.calendar.list_events",
                5,
            ),
            (
                "Read calendar availability",
                ObjectiveKind.RETRIEVE,
                (ObjectiveOperation.RETRIEVE,),
                ("calendar", "availability"),
                "google.calendar.freebusy",
                5,
            ),
            (
                "List the user's calendars",
                ObjectiveKind.RETRIEVE,
                (ObjectiveOperation.RETRIEVE,),
                ("calendar",),
                "google.calendar.list_calendars",
                5,
            ),
            (
                "List personal tasks",
                ObjectiveKind.RETRIEVE,
                (ObjectiveOperation.RETRIEVE,),
                ("task",),
                "tasks.list",
                4,
            ),
            (
                "List workflows",
                ObjectiveKind.RETRIEVE,
                (ObjectiveOperation.RETRIEVE,),
                ("workflow",),
                "workflow.list",
                5,
            ),
            (
                "Read a workflow run",
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
            (
                "List workflow versions",
                ObjectiveKind.RETRIEVE,
                (ObjectiveOperation.RETRIEVE,),
                ("workflow version",),
                "workflow.version.list",
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
            ("Send Dad a mail message", ("email", "message"), "google.gmail.send_email", 4),
            ("Draft a mail message for Dad", ("email", "message"), "google.gmail.create_draft", 4),
            ("Create a calendar event", ("calendar", "event"), "google.calendar.create_event", 4),
            ("Create a personal task", ("task",), "tasks.create", 4),
            ("Create a workflow", ("workflow",), "workflow.create", 4),
            ("Execute the selected workflow now", ("workflow",), "workflow.run.start", 5),
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
                "Search mail messages from Dad | resources email message | operations retrieve",
                "google.gmail.search",
                ("google.gmail.send_email", "google.gmail.create_draft", "google.gmail.modify_labels"),
            ),
            (
                "List calendar events tomorrow | resources calendar event | operations retrieve",
                "google.calendar.list_events",
                ("google.calendar.create_event", "google.calendar.update_event", "google.calendar.delete_event"),
            ),
            (
                "List workflows | resources workflow | operations retrieve",
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
