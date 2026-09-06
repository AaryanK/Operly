import unittest

from packages.agent_runtime.real_world_implicit_cases import IMPLICIT_SEMANTIC_CASES


class ImplicitSemanticCorpusTests(unittest.TestCase):
    def test_exactly_one_hundred_unique_cases(self):
        self.assertEqual(len(IMPLICIT_SEMANTIC_CASES), 100)
        self.assertEqual(len({case.case_id for case in IMPLICIT_SEMANTIC_CASES}), 100)
        self.assertEqual(len({case.prompt for case in IMPLICIT_SEMANTIC_CASES}), 100)

    def test_both_scopes_and_no_tool_boundaries_are_present(self):
        scopes = {case.scope for case in IMPLICIT_SEMANTIC_CASES}
        self.assertEqual(scopes, {"personal", "workspace"})
        self.assertTrue(any(not case.external for case in IMPLICIT_SEMANTIC_CASES))
        self.assertTrue(any(case.external and not case.mutation for case in IMPLICIT_SEMANTIC_CASES))
        self.assertTrue(any(case.mutation for case in IMPLICIT_SEMANTIC_CASES))
        self.assertTrue(any(case.wait for case in IMPLICIT_SEMANTIC_CASES))
        self.assertTrue(any(case.dispatch == "agent_loop" for case in IMPLICIT_SEMANTIC_CASES))

    def test_most_cases_are_keyword_light(self):
        obvious = ("email", "gmail", "inbox", "calendar", "workflow", "task", "workspace", "canva", "discord", "browser")
        keyword_light = [
            case for case in IMPLICIT_SEMANTIC_CASES
            if not any(token in case.prompt.lower() for token in obvious)
        ]
        self.assertGreaterEqual(len(keyword_light), 80)

    def test_majority_expect_a_real_capability(self):
        with_expected = [case for case in IMPLICIT_SEMANTIC_CASES if case.expected_any]
        self.assertGreaterEqual(len(with_expected), 85)
        for case in with_expected:
            for capability_id in case.expected_any:
                self.assertNotIn(capability_id, case.prompt)

    def test_multilingual_implicit_cases_are_present(self):
        tagged = {
            style
            for case in IMPLICIT_SEMANTIC_CASES
            for style in case.styles
            if style in {"french", "dutch", "nepali", "spanish", "german", "hinglish", "arabic", "japanese"}
        }
        self.assertEqual(tagged, {"french", "dutch", "nepali", "spanish", "german", "hinglish", "arabic", "japanese"})


if __name__ == "__main__":
    unittest.main()
