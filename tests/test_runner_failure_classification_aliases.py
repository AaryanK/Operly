import unittest

from packages.custom_software.runner_service import (
    _failure_classification,
    _failure_state_for,
)


class RunnerFailureClassificationAliasTests(unittest.TestCase):
    def test_health_failure_alias_uses_canonical_repairable_classification(self):
        result = {
            "failureEvidence": {
                "classification": "health_failure",
                "message": "Configured backend health check did not pass",
            }
        }

        classification = _failure_classification(result)

        self.assertEqual(classification, "health_check_failure")
        self.assertEqual(
            _failure_state_for("health_checking", classification),
            "health_check_failed",
        )

    def test_acceptance_failure_alias_uses_canonical_repairable_classification(self):
        result = {
            "failureEvidence": {
                "classification": "acceptance_failure",
                "message": "Preview root acceptance failed",
            }
        }

        classification = _failure_classification(result)

        self.assertEqual(classification, "acceptance_test_failure")
        self.assertEqual(
            _failure_state_for("acceptance_testing", classification),
            "acceptance_failed",
        )


if __name__ == "__main__":
    unittest.main()
