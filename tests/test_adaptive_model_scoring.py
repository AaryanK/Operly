import unittest

from packages.model_runtime.discovery import installed_model_discoverers
from packages.model_runtime.scoring import ModelScorer


class _Traits:
    def __init__(self, quality_class="balanced"):
        self.quality_class = quality_class


class _Model:
    def __init__(self, provider, model_id, *, priority=50, latency=500):
        self.provider = provider
        self.provider_model_id = model_id
        self.id = f"{provider}:{model_id}"
        self.priority = priority
        self.verified_latency_ms = latency
        self.tags = frozenset({"free"})
        self.traits = _Traits()


class AdaptiveModelScoringTests(unittest.TestCase):
    def test_all_configured_provider_discoverers_are_installed(self):
        self.assertEqual(
            set(installed_model_discoverers()),
            {"openrouter", "ollama", "groq", "gemini", "nvidia"},
        )

    def test_rate_limited_route_drops_and_another_route_wins(self):
        scorer = ModelScorer()
        first = _Model("ollama", "gpt-oss:20b", priority=10, latency=100)
        second = _Model("nvidia", "deepseek-ai/deepseek-r1", priority=20, latency=300)

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
        model = _Model("nvidia", "deepseek-ai/deepseek-r1")
        initial = scorer.rank([model], task_type="coding")[0].score

        scorer.record_success(model, latency_ms=80, task_type="coding")
        learned = scorer.rank([model], task_type="coding")[0].score

        self.assertGreater(learned, initial)

    def test_same_canonical_model_routes_can_be_scored_independently(self):
        scorer = ModelScorer()
        nvidia = _Model("nvidia", "deepseek-ai/deepseek-r1")
        router = _Model("openrouter", "deepseek/deepseek-r1")

        scorer.record_failure(nvidia, classification="rate_limited")
        ranked = scorer.rank([nvidia, router])

        self.assertEqual([row.model.provider for row in ranked], ["openrouter"])


if __name__ == "__main__":
    unittest.main()
