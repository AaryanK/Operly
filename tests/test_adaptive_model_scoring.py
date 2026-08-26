import os
import unittest
from unittest.mock import patch

from packages.model_runtime.discovery import installed_model_discoverers
from packages.model_runtime.scoring import ModelScorer


class _Traits:
    def __init__(self, quality_class="balanced"):
        self.quality_class = quality_class


class _Model:
    def __init__(self, provider, model_id, *, priority=50, latency=500, free=True):
        self.provider = provider
        self.provider_model_id = model_id
        self.id = f"{provider}:{model_id}"
        self.priority = priority
        self.verified_latency_ms = latency
        self.tags = frozenset({"free"}) if free else frozenset()
        self.traits = _Traits()


class AdaptiveModelScoringTests(unittest.TestCase):
    def test_all_configured_provider_discoverers_are_installed(self):
        self.assertEqual(
            set(installed_model_discoverers()),
            {"openrouter", "ollama", "groq", "gemini", "nvidia"},
        )

    def test_paid_or_unknown_cost_route_is_never_ranked_by_default(self):
        scorer = ModelScorer()
        paid = _Model("openrouter", "expensive/model", priority=0, latency=1, free=False)
        free = _Model("groq", "openai/gpt-oss-20b", priority=100, latency=1000, free=True)

        ranked = scorer.rank([paid, free], task_type="business_agent")

        self.assertEqual([row.model for row in ranked], [free])

    def test_free_only_policy_can_be_explicitly_disabled_later(self):
        scorer = ModelScorer()
        paid = _Model("openrouter", "paid/model", free=False)
        with patch.dict(os.environ, {"OPERLY_FREE_MODELS_ONLY": "0"}, clear=False):
            ranked = scorer.rank([paid], task_type="business_agent")
        self.assertEqual([row.model for row in ranked], [paid])

    def test_rate_limited_route_drops_and_another_route_wins(self):
        scorer = ModelScorer()
        first = _Model("ollama", "gpt-oss:20b", priority=10, latency=100)
        second = _Model("nvidia", "deepseek-ai/deepseek-v4-flash-0731", priority=20, latency=300)

        before = scorer.rank([first, second], task_type="business_agent")
        self.assertIs(before[0].model, first)

        scorer.record_failure(
            first,
            classification="rate_limited",
            task_type="business_agent",
        )
        after = scorer.rank([first, second], task_type="business_agent")

        self.assertEqual(len(after), 1)
        self.assertIs(after[0].model, second)

    def test_success_improves_route_runtime_score(self):
        scorer = ModelScorer()
        model = _Model("nvidia", "deepseek-ai/deepseek-v4-flash-0731")
        initial = scorer.rank([model], task_type="coding")[0].score

        scorer.record_success(model, latency_ms=80, task_type="coding")
        learned = scorer.rank([model], task_type="coding")[0].score

        self.assertGreater(learned, initial)

    def test_same_canonical_model_routes_can_be_scored_independently(self):
        scorer = ModelScorer()
        nvidia = _Model("nvidia", "deepseek-ai/deepseek-v4-flash-0731")
        router = _Model("openrouter", "deepseek/deepseek-r1:free")

        scorer.record_failure(nvidia, classification="rate_limited")
        ranked = scorer.rank([nvidia, router])

        self.assertEqual([row.model.provider for row in ranked], ["openrouter"])


if __name__ == "__main__":
    unittest.main()
