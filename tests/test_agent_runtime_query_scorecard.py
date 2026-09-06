from __future__ import annotations

import unittest

from packages.personal_modules.runtime import build_personal_runtime
from packages.security.execution_context import (
    ExecutionContext,
    PERSONAL_EXECUTION_PERMISSIONS,
    ScopeKind,
)
from packages.security.surfaces import SurfaceKind
from packages.workspace_modules.tools.runtime import build_workspace_runtime


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


def workspace_owner_context() -> ExecutionContext:
    return ExecutionContext(
        workspace_id="workspace-1",
        user_id="user-1",
        membership_id="membership-1",
        role="owner",
        permissions=frozenset(),
        channel="web",
        surface=SurfaceKind.WORKSPACE_PRIVATE,
        conversation_id="conversation-1",
        scope_kind=ScopeKind.WORKSPACE,
        principal_id="user:user-1",
    )


class RuntimeQueryGeneralizationScorecard(unittest.TestCase):
    """Natural-language capability discovery scorecard over current real registries.

    This deliberately mixes previously troublesome phrasings with unseen paraphrases.
    Every case prints the actual rank and top candidates before the aggregate assertion,
    so CI is also a diagnostic report instead of only a red/green gate.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.personal_registry = build_personal_runtime().registry
        cls.workspace_registry = build_workspace_runtime().registry
        cls.personal = personal_context()
        cls.workspace = workspace_owner_context()

    def _score(self, scope: str, query: str, expected: str, top: int) -> str | None:
        registry = self.personal_registry if scope == "personal" else self.workspace_registry
        context = self.personal if scope == "personal" else self.workspace
        ids = [
            spec.id
            for spec in registry.search(
                query,
                context=context,
                effective_only=True,
                limit=12,
            )
        ]
        rank = ids.index(expected) + 1 if expected in ids else None
        print(
            "QUERY_SCORECARD"
            f" scope={scope} expected={expected} rank={rank} top={top}"
            f" query={query!r} candidates={ids[:8]}"
        )
        if rank is None:
            return f"{scope}: {expected} missing for {query!r}; got {ids}"
        if rank > top:
            return f"{scope}: {expected} rank {rank} > {top} for {query!r}; got {ids}"
        return None

    def test_previous_and_unseen_queries(self):
        cases = (
            # Previously troublesome/raw compatibility cases.
            ("personal", "look through my inbox for the tuition receipt", "google.gmail.search", 5),
            ("personal", "show me the versions of this workflow", "workflow.version.list", 5),
            ("personal", "show recent workflow runs", "workflow.run.list", 5),
            ("personal", "disable this workflow", "workflow.disable", 5),
            ("personal", "enable this workflow", "workflow.enable", 5),
            # New personal paraphrases intended to probe generalization.
            ("personal", "pull up the email where the bursar sent my receipt", "google.gmail.search", 5),
            ("personal", "what appointments do I have tomorrow morning", "google.calendar.list_events", 5),
            ("personal", "show the last few workflow executions", "workflow.run.list", 5),
            ("personal", "list older revisions of this workflow", "workflow.version.list", 5),
            ("personal", "what is on my task list", "tasks.list", 4),
            ("personal", "turn this workflow off", "workflow.disable", 5),
            ("personal", "switch this workflow back on", "workflow.enable", 5),
            ("personal", "write an email draft to Dad", "google.gmail.create_draft", 4),
            ("personal", "schedule a calendar meeting tomorrow", "google.calendar.create_event", 4),
            # Existing Workspace precision cases plus unseen paraphrases.
            ("workspace", "Find Acme in this workspace | resources customer workspace contact | operations retrieve", "workspace.search", 4),
            ("workspace", "Create a 500 dollar invoice for design work due in 14 days | resources invoice finance | operations act", "workspace.finance.invoice.create_simple", 5),
            ("workspace", "find Northstar supplier in this workspace | resources supplier workspace | operations retrieve", "workspace.search", 4),
            ("workspace", "show the selected customer's complete profile | resources customer crm contact | operations retrieve", "workspace.customer.snapshot", 5),
            ("workspace", "make an invoice for 750 dollars due next Friday | resources invoice finance | operations act", "workspace.finance.invoice.create_simple", 5),
            ("workspace", "record the payment for invoice INV-1042 | resources payment invoice finance | operations act", "workspace.finance.payment.record", 5),
            ("workspace", "what needs my attention right now | resources task appointment support inventory | operations retrieve", "workspace.attention.list", 5),
        )
        failures: list[str] = []
        for scope, query, expected, top in cases:
            failure = self._score(scope, query, expected, top)
            if failure:
                failures.append(failure)
        self.assertFalse(failures, "\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
