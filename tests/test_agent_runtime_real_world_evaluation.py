from __future__ import annotations

import unittest
from collections import Counter

from packages.agent_runtime.real_world_evaluation import CURATED_CASES, _workspace_os_cases, corpus_inventory
from packages.agent_runtime.real_world_evaluation_extra import EXTRA_CURATED_CASES
from packages.personal_modules.runtime import build_personal_runtime
from packages.workspace_modules.tools.runtime import build_workspace_runtime


class RealWorldObjectiveCorpusTests(unittest.TestCase):
    def _cases(self):
        return CURATED_CASES + EXTRA_CURATED_CASES + _workspace_os_cases()

    def test_every_current_builtin_capability_has_a_human_prompt(self):
        cases = self._cases()
        covered = {cap for case in cases for cap in case.expected_any}
        builtins = {
            spec.id
            for runtime in (build_personal_runtime(), build_workspace_runtime())
            for spec in runtime.registry.all()
        }
        missing = sorted(builtins - covered)
        self.assertEqual(
            missing,
            [],
            msg=(
                "Every currently registered built-in capability must have at least one "
                "raw human-language routing case. Missing: " + ", ".join(missing)
            ),
        )

    def test_workspace_os_record_contracts_are_generated_exhaustively(self):
        expected = {
            spec.id
            for spec in build_workspace_runtime().registry.all()
            if spec.id.startswith("workspace_os.")
        }
        generated = {cap for case in _workspace_os_cases() for cap in case.expected_any}
        self.assertEqual(generated, expected)
        self.assertGreater(len(generated), 50, "Workspace OS regression corpus became suspiciously small")

    def test_corpus_is_large_diverse_raw_and_unique(self):
        cases = self._cases()
        prompts = [case.prompt for case in cases]
        self.assertEqual(len(prompts), len(set(prompts)), "Duplicate prompts weaken the evaluation")
        self.assertGreaterEqual(len(CURATED_CASES) + len(EXTRA_CURATED_CASES), 120)
        self.assertGreaterEqual(len(cases), 250)

        families = Counter(case.family for case in cases)
        styles = Counter(style for case in cases for style in case.styles)
        scopes = Counter(case.scope for case in cases)
        self.assertGreaterEqual(len(families), 15)
        self.assertGreaterEqual(len(styles), 20)
        self.assertGreater(scopes["personal"], 30)
        self.assertGreater(scopes["workspace"], 80)

        required_styles = {
            "casual",
            "slang",
            "fragment",
            "typo",
            "negation",
            "contextual",
            "pronoun",
            "voice_dictation",
            "business_shorthand",
            "developer_slang",
            "emoji",
            "code_switch",
            "fuzzy_time",
            "compound",
        }
        self.assertFalse(required_styles - set(styles), f"Missing raw-prompt styles: {required_styles - set(styles)}")

    def test_prompts_do_not_cheat_with_literal_capability_ids(self):
        for case in self._cases():
            lowered = case.prompt.lower()
            for capability_id in case.expected_any:
                with self.subTest(case=case.case_id, capability=capability_id):
                    self.assertNotIn(
                        capability_id.lower(),
                        lowered,
                        "A natural-language benchmark prompt must not leak the exact capability id",
                    )

    def test_no_tool_traps_cover_both_scopes_and_tool_nouns(self):
        no_tool = [case for case in self._cases() if not case.external]
        self.assertGreaterEqual(len(no_tool), 10)
        self.assertEqual({case.scope for case in no_tool}, {"personal", "workspace"})
        joined = " ".join(case.prompt.lower() for case in no_tool)
        for noun in ("email", "calendar", "workflow", "file", "invoice", "customer", "task"):
            self.assertIn(noun, joined)

    def test_mutation_retrieval_compound_wait_and_multilingual_are_present(self):
        cases = self._cases()
        self.assertTrue(any(case.mutation for case in cases))
        self.assertTrue(any(case.external and not case.mutation and not case.wait for case in cases))
        self.assertTrue(any(case.dispatch == "agent_loop" for case in cases))
        self.assertTrue(any(case.wait for case in cases))
        multilingual = [case for case in cases if case.family == "multilingual"]
        self.assertGreaterEqual(len(multilingual), 8)

    def test_inventory_report_is_stable_and_informative(self):
        inventory = corpus_inventory()
        # corpus_inventory intentionally describes the primary corpus; extra edge cases
        # are kept separate. The counts are still useful for operator diagnostics.
        self.assertGreater(inventory["curated"], 100)
        self.assertGreater(inventory["workspace_os_generated"], 50)
        self.assertIn("personal", inventory["scopes"])
        self.assertIn("workspace", inventory["scopes"])


if __name__ == "__main__":
    unittest.main()
