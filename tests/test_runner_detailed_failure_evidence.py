import unittest

from packages.runtime_plugins.runner_service import _enrich_failure_evidence


class RunnerDetailedFailureEvidenceTests(unittest.TestCase):
    def test_generic_python_test_failure_uses_detailed_testing_event(self):
        result = {
            "failureEvidence": {
                "classification": "test_failure",
                "message": "Python tests failed",
            }
        }
        response = {
            "events": [
                {
                    "state": "testing",
                    "exitCode": 1,
                    "message": "FAIL: test_clock_in\nAssertionError: expected 200, got 500",
                }
            ]
        }

        enriched = _enrich_failure_evidence(result, response, "test_failure")

        evidence = enriched["failureEvidence"]
        self.assertIn("AssertionError: expected 200, got 500", evidence["message"])
        self.assertEqual(evidence["runnerEventState"], "testing")
        self.assertEqual(evidence["runnerExitCode"], 1)
        self.assertEqual(result["failureEvidence"]["message"], "Python tests failed")

    def test_specific_failure_message_is_not_overwritten(self):
        result = {
            "failureEvidence": {
                "classification": "test_failure",
                "message": "AssertionError: already specific",
            }
        }
        response = {
            "events": [{"state": "testing", "message": "different detail", "exitCode": 1}]
        }

        enriched = _enrich_failure_evidence(result, response, "test_failure")

        self.assertEqual(
            enriched["failureEvidence"]["message"],
            "AssertionError: already specific",
        )


if __name__ == "__main__":
    unittest.main()
