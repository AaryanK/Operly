from __future__ import annotations

import unittest
from unittest.mock import patch

from packages.kernel.contracts import CapabilityRisk, CapabilitySpec
from packages.kernel.registry import CapabilityRegistry
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind
from packages.workspace_modules.tools.runtime import build_workspace_runtime


def _schema() -> dict:
    return {"type": "object", "properties": {}}


def _spec(
    capability_id: str,
    *,
    description: str,
    tags: tuple[str, ...] = (),
    permission: str | None = None,
    risk: CapabilityRisk = CapabilityRisk.READ_ONLY,
) -> CapabilitySpec:
    return CapabilitySpec(
        id=capability_id,
        version="1.0.0",
        display_name=capability_id.replace(".", " "),
        description=description,
        provider_id="test.provider",
        scopes=frozenset({"workspace"}),
        input_schema=_schema(),
        output_schema=_schema(),
        permissions=(permission,) if permission else (),
        risk=risk,
        tags=frozenset(tags),
        resource_scope="workspace",
    )


def owner_context() -> ExecutionContext:
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


def member_context() -> ExecutionContext:
    return ExecutionContext(
        workspace_id="workspace-1",
        user_id="user-1",
        membership_id="membership-1",
        role="member",
        permissions=frozenset(),
        channel="web",
        surface=SurfaceKind.WORKSPACE_PRIVATE,
        conversation_id="conversation-1",
        scope_kind=ScopeKind.WORKSPACE,
        principal_id="user:user-1",
    )


class WorkspaceCapabilityRetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = build_workspace_runtime().registry
        cls.context = owner_context()

    def _ids(self, query: str) -> list[str]:
        return [
            spec.id
            for spec in self.registry.search(
                query,
                context=self.context,
                effective_only=True,
                limit=12,
            )
        ]

    def _assert_near_top(self, query: str, capability_id: str, *, top: int = 5) -> None:
        ids = self._ids(query)
        self.assertIn(
            capability_id,
            ids,
            msg=f"{capability_id!r} missing for {query!r}; got {ids}",
        )
        self.assertLess(
            ids.index(capability_id),
            top,
            msg=f"{capability_id!r} ranked too low: {ids}",
        )

    def _assert_any_near_top(
        self,
        query: str,
        capability_ids: tuple[str, ...],
        *,
        top: int = 4,
    ) -> None:
        ids = self._ids(query)
        near = set(ids[:top])
        self.assertTrue(
            near.intersection(capability_ids),
            msg=f"none of {capability_ids!r} ranked in top {top} for {query!r}; got {ids}",
        )

    def test_workspace_search_surfaces_cross_entity_or_precise_search_capability(self):
        # Workspace record LIST contracts intentionally accept a q filter and advertise
        # `search <entity>` aliases. When the semantic compiler identifies the entity,
        # an entity-specific searchable list can be more precise than workspace.search.
        # The retriever should surface at least one correct search path near the top,
        # rather than hard-routing every `Search workspace ...` objective to one ID.
        cases = (
            (
                "Find Acme in this workspace | resources customer workspace contact | operations retrieve",
                ("workspace.search", "workspace_os.crm.contacts.list"),
            ),
            (
                "Search the workspace for invoice INV-1042 | resources file | operations retrieve",
                ("workspace.search", "workspace_os.finance.invoices.list"),
            ),
            (
                "Find the launch project in this workspace | resources project workspace | operations retrieve",
                ("workspace.search", "workspace_os.projects.projects.list"),
            ),
            (
                "Search this workspace for supplier Northstar | resources supplier workspace | operations retrieve",
                ("workspace.search", "workspace_os.suppliers.suppliers.list"),
            ),
        )
        for query, expected in cases:
            with self.subTest(query=query):
                self._assert_any_near_top(query, expected, top=4)
                # Keep the broad cross-entity search available for agent recovery even
                # when a narrower entity search ranks higher.
                self.assertIn("workspace.search", self._ids(query))

    def test_workspace_business_actions_and_attention_rank_correctly(self):
        cases = (
            (
                "What needs my attention in the business today | resources email calendar task file | operations retrieve",
                "workspace.attention.list",
            ),
            (
                "Create a 500 dollar invoice for design work due in 14 days | resources invoice finance | operations act",
                "workspace.finance.invoice.create_simple",
            ),
            (
                "Record a 500 dollar payment against invoice INV-1042 | resources payment invoice finance | operations act",
                "workspace.finance.payment.record",
            ),
            (
                "Show the full snapshot for the selected customer | resources customer contact crm | operations retrieve",
                "workspace.customer.snapshot",
            ),
        )
        for query, expected in cases:
            with self.subTest(query=query):
                self._assert_near_top(query, expected, top=5)


class ScalableCapabilityIndexTests(unittest.TestCase):
    def test_exact_semantic_read_action_beats_same_resource_siblings(self):
        registry = CapabilityRegistry(
            (
                _spec(
                    "future.orion.search",
                    description="Search orion records by arbitrary criteria",
                    tags=("search", "orion", "record"),
                ),
                _spec(
                    "future.orion.list",
                    description="List orion records as a collection",
                    tags=("list", "orion", "record"),
                ),
                _spec(
                    "future.orion.read",
                    description="Read one selected orion record",
                    tags=("read", "orion", "record"),
                ),
            )
        )
        cases = (
            (
                "Search orion records | resources orion record | operations retrieve",
                "future.orion.search",
            ),
            (
                "List orion records | resources orion record | operations retrieve",
                "future.orion.list",
            ),
            (
                "Read selected orion record | resources orion record | operations retrieve",
                "future.orion.read",
            ),
        )
        for query, expected in cases:
            with self.subTest(query=query):
                ids = [
                    spec.id
                    for spec in registry.search(
                        query,
                        context=owner_context(),
                        effective_only=True,
                        limit=12,
                    )
                ]
                self.assertEqual(ids[0], expected, msg=f"{query!r}: {ids}")

    def test_related_actions_surface_from_capability_family_without_domain_synonyms(self):
        registry = CapabilityRegistry(
            (
                _spec(
                    "future.messaging.search",
                    description="Search nebula messages across this account",
                    tags=("search", "nebula"),
                ),
                _spec(
                    "future.messaging.read_message",
                    description="Read one selected message by identifier",
                    tags=("read",),
                ),
                _spec(
                    "future.messaging.send_message",
                    description="Send one selected message",
                    tags=("send",),
                    risk=CapabilityRisk.MEDIUM,
                ),
            )
        )
        ids = [
            spec.id
            for spec in registry.search(
                "Find nebula | resources nebula | operations retrieve",
                context=owner_context(),
                effective_only=True,
                limit=12,
            )
        ]
        self.assertIn("future.messaging.search", ids)
        self.assertIn("future.messaging.read_message", ids)
        self.assertNotIn(
            "future.messaging.send_message",
            ids,
            "pure retrieval should not expose a mutating sibling contract",
        )

    def test_large_catalog_does_not_score_every_capability(self):
        registry = CapabilityRegistry()
        for index in range(20_000):
            registry.register(
                _spec(
                    f"future.catalog.item_{index}.list",
                    description=f"List generic future catalog resource {index}",
                    tags=("list", "future"),
                )
            )
        registry.register(
            _spec(
                "future.needle.search",
                description="Search the rare quasarneedle resource across the future catalog",
                tags=("search", "quasarneedle"),
            )
        )

        original = registry._search_index.score
        with patch.object(registry._search_index, "score", wraps=original) as score:
            results = registry.search(
                "Find quasarneedle | resources quasarneedle | operations retrieve",
                context=owner_context(),
                effective_only=True,
                limit=12,
            )
        self.assertEqual(results[0].id, "future.needle.search")
        self.assertLess(
            score.call_count,
            100,
            "search regressed to near-full catalog scoring",
        )

    def test_semantic_candidate_provider_is_untrusted_and_policy_filtered(self):
        secret = _spec(
            "future.secret.search",
            description="Search secret records",
            permission="secret:read",
            tags=("search",),
        )
        allowed = _spec(
            "future.public.search",
            description="Search public semantic records",
            tags=("search", "public"),
        )

        class Provider:
            def candidate_ids(self, query: str, *, limit: int):
                return (
                    "future.secret.search",
                    "future.public.search",
                    "made.up.capability",
                )

        registry = CapabilityRegistry(
            (secret, allowed),
            semantic_candidate_provider=Provider(),
        )
        ids = [
            spec.id
            for spec in registry.search(
                "semantically similar phrase with no lexical overlap",
                context=member_context(),
                effective_only=True,
                limit=12,
            )
        ]
        self.assertIn("future.public.search", ids)
        self.assertNotIn("future.secret.search", ids)
        self.assertNotIn("made.up.capability", ids)


if __name__ == "__main__":
    unittest.main()
