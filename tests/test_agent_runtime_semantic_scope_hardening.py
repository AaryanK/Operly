import unittest

from packages.agent_runtime.inference import OBJECTIVE_SEMANTIC_ROUTING_GUIDANCE
from packages.agent_runtime.real_world_evaluation import _context, _registry
from packages.kernel.capability_search import action_facets


class SemanticScopeGuidanceTests(unittest.TestCase):
    def test_guidance_uses_scope_as_semantic_grounding_not_authority(self):
        guidance = OBJECTIVE_SEMANTIC_ROUTING_GUIDANCE.lower()
        self.assertIn("scope_kind", guidance)
        self.assertIn("interpretive grounding", guidance)
        self.assertIn("never be copied into output", guidance)
        self.assertIn("generic explanation", guidance)
        self.assertIn("current workspace state", guidance)
        self.assertIn("personal state", guidance)

    def test_guidance_requires_specific_provider_neutral_retrieval_facets(self):
        guidance = OBJECTIVE_SEMANTIC_ROUTING_GUIDANCE.lower()
        self.assertIn("retrieval facets", guidance)
        self.assertIn("provider-neutral", guidance)
        self.assertIn("avoid resource hints", guidance)
        self.assertIn("accounts-receivable invoices", guidance)
        self.assertIn("workspace members/access", guidance)
        self.assertIn("computer runtime", guidance)

    def test_supplied_context_resolution_is_not_an_external_lookup(self):
        guidance = OBJECTIVE_SEMANTIC_ROUTING_GUIDANCE.lower()
        self.assertIn("supplied relevant context", guidance)
        self.assertIn("not an additional external retrieval step", guidance)
        self.assertIn("use composite only when completion genuinely requires multiple external operations", guidance)


class GenericOperationOntologyTests(unittest.TestCase):
    def test_domain_agnostic_state_change_verbs_align_to_canonical_actions(self):
        self.assertEqual(action_facets("adjust quantity"), frozenset({"update"}))
        self.assertEqual(action_facets("deploy release"), frozenset({"execute"}))
        self.assertEqual(action_facets("invite teammate"), frozenset({"create"}))
        self.assertEqual(action_facets("revoke invitation"), frozenset({"delete"}))
        self.assertEqual(action_facets("rollback release"), frozenset({"update"}))


class CompiledWorkspaceRetrievalTests(unittest.TestCase):
    def _ids(self, query: str) -> list[str]:
        context = _context("workspace")
        return [
            spec.id
            for spec in _registry("workspace").search(
                query,
                context=context,
                effective_only=True,
                limit=12,
            )
        ]

    def assertSurfaced(self, expected: str, query: str) -> None:  # noqa: N802
        ids = self._ids(query)
        self.assertIn(expected, ids, msg=f"{expected} missing from {ids}")

    def test_specific_model_compiled_facets_recover_implicit_workspace_capabilities(self):
        cases = (
            (
                "workspace.inventory.adjust",
                "Update inventory stock quantity after a physical count | "
                "resources inventory stock item adjustment physical count | operations act",
            ),
            (
                "workspace.activity.list",
                "Read recent workspace activity and changes | "
                "resources workspace activity change history | operations retrieve",
            ),
            (
                "workspace.summary.read",
                "Read current business summary | resources business summary workspace | operations retrieve",
            ),
            (
                "workspace.members.list",
                "List current workspace members and access | "
                "resources workspace member access team | operations retrieve",
            ),
            (
                "studio.solution.deploy",
                "Execute deployment for project proj-7 | "
                "resources studio deployment solution project | operations act",
            ),
            (
                "computer.runtime.status",
                "Read computer runtime status for cs-1 | "
                "resources computer runtime session | operations retrieve",
            ),
            (
                "computer.git.diff",
                "Read source control changes in cs-1 | "
                "resources git repository diff source control | operations retrieve",
            ),
            (
                "workspace_os.finance.invoices.list",
                "List unpaid invoices and amounts customers still owe | "
                "resources invoice accounts receivable customer balance | operations retrieve",
            ),
        )
        for expected, query in cases:
            with self.subTest(expected=expected):
                self.assertSurfaced(expected, query)


if __name__ == "__main__":
    unittest.main()
