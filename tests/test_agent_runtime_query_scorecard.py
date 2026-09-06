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


class RuntimeCompiledObjectiveRetrievalScorecard(unittest.TestCase):
    """Capability discovery scorecard over model-compiled semantic ObjectiveIR queries.

    Raw human language is the ObjectiveInterpreter's responsibility. The Kernel search
    contract intentionally receives a concise canonical semantic query so it does not
    grow language-specific synonym tables. The separate live objective evaluator covers
    raw slang, typos, multilingual input and code switching end-to-end.
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
            "COMPILED_QUERY_SCORECARD"
            f" scope={scope} expected={expected} rank={rank} top={top}"
            f" query={query!r} candidates={ids[:8]}"
        )
        if rank is None:
            return f"{scope}: {expected} missing for {query!r}; got {ids}"
        if rank > top:
            return f"{scope}: {expected} rank {rank} > {top} for {query!r}; got {ids}"
        return None

    def test_model_compiled_semantics_retrieve_expected_capabilities(self):
        cases = (
            # Personal mail/calendar/tasks/workflow semantics. These are the canonical
            # representations the model should produce from any human language.
            ("personal", "Search mail messages for tuition receipt | resources email message | operations retrieve", "google.gmail.search", 4),
            ("personal", "Search mail messages from bursar for receipt | resources email message | operations retrieve", "google.gmail.search", 4),
            ("personal", "Read selected mail message | resources email message | operations retrieve", "google.gmail.read_message", 4),
            ("personal", "Draft mail message to Dad | resources email message draft | operations act", "google.gmail.create_draft", 4),
            ("personal", "Send mail message to Dad | resources email message | operations act", "google.gmail.send_email", 4),
            ("personal", "List calendar events tomorrow morning | resources calendar event | operations retrieve", "google.calendar.list_events", 4),
            ("personal", "Read calendar availability Friday afternoon | resources calendar availability | operations retrieve", "google.calendar.freebusy", 4),
            ("personal", "Create calendar event tomorrow | resources calendar event | operations act", "google.calendar.create_event", 4),
            ("personal", "List personal tasks | resources task | operations retrieve", "tasks.list", 4),
            ("personal", "Create personal task | resources task | operations act", "tasks.create", 4),
            ("personal", "List workflow runs | resources workflow run | operations retrieve", "workflow.run.list", 5),
            ("personal", "List workflow versions | resources workflow version | operations retrieve", "workflow.version.list", 5),
            ("personal", "Disable workflow | resources workflow | operations act", "workflow.disable", 5),
            ("personal", "Enable workflow | resources workflow | operations act", "workflow.enable", 5),
            # Workspace/SMB capability precision.
            ("workspace", "Search workspace for Acme customer | resources customer workspace contact | operations retrieve", "workspace.search", 4),
            ("workspace", "Search workspace for invoice INV-1042 | resources invoice workspace | operations retrieve", "workspace.search", 4),
            ("workspace", "Search workspace for supplier Northstar | resources supplier workspace | operations retrieve", "workspace.search", 4),
            ("workspace", "Read selected customer complete snapshot | resources customer crm contact | operations retrieve", "workspace.customer.snapshot", 5),
            ("workspace", "List business attention items | resources task appointment support inventory | operations retrieve", "workspace.attention.list", 5),
            ("workspace", "Create invoice for 500 dollars due in 14 days | resources invoice finance | operations act", "workspace.finance.invoice.create_simple", 5),
            ("workspace", "Record payment against invoice INV-1042 | resources payment invoice finance | operations act", "workspace.finance.payment.record", 5),
        )
        failures: list[str] = []
        for scope, query, expected, top in cases:
            failure = self._score(scope, query, expected, top)
            if failure:
                failures.append(failure)
        self.assertFalse(failures, "\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
