import unittest
from datetime import datetime

from apps.api.admin_ai_usage import _bucket_key, _usage_values, normalize_usage_range


class AdminAiUsageTests(unittest.TestCase):
    def test_normalizes_supported_ranges(self):
        for value in ("1h", "24h", "7d", "30d", "all"):
            self.assertEqual(normalize_usage_range(value), value)
        self.assertEqual(normalize_usage_range("7D"), "7d")
        self.assertEqual(normalize_usage_range("unexpected"), "24h")
        self.assertEqual(normalize_usage_range(None), "24h")

    def test_accepts_openai_style_usage(self):
        self.assertEqual(
            _usage_values(
                {
                    "prompt_tokens": 120,
                    "completion_tokens": 30,
                    "total_tokens": 150,
                }
            ),
            (120, 30, 150),
        )

    def test_accepts_provider_neutral_usage_and_computes_total(self):
        self.assertEqual(
            _usage_values({"input_tokens": 80, "output_tokens": 20}),
            (80, 20, 100),
        )

    def test_uses_five_minute_hour_buckets(self):
        value = datetime(2026, 8, 25, 4, 18, 47)
        self.assertEqual(_bucket_key(value, "1h"), "2026-08-25T04:15:00")
        self.assertEqual(_bucket_key(value, "24h"), "2026-08-25T04:00:00")
        self.assertEqual(_bucket_key(value, "7d"), "2026-08-25")


if __name__ == "__main__":
    unittest.main()
