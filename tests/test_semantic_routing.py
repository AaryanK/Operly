import json
import unittest

from packages.model_runtime.semantic_router import SemanticRouter, SemanticRoutingError


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def chat(self, messages, tools):
        self.calls += 1
        return {"content": self.responses.pop(0)}


class SemanticRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_can_route_semantic_paraphrase_without_keyword_gate(self):
        client = FakeClient([
            json.dumps({
                "domainMatch": True,
                "known": True,
                "route": "software_build",
                "reason": "The request is fully covered by the supplied software capability.",
            })
        ])
        decision = await SemanticRouter(client).decide(
            request="Give staff a protected attendance portal.",
            domain="software operations",
            routes={"software_build": "build a governed software project"},
        )
        self.assertTrue(decision.domain_match)
        self.assertTrue(decision.known)
        self.assertEqual(decision.route_id, "software_build")
        self.assertEqual(client.calls, 1)

    async def test_model_can_mark_in_domain_request_unknown_for_capability_discovery(self):
        client = FakeClient([
            json.dumps({
                "domainMatch": True,
                "known": False,
                "route": None,
                "reason": "No supplied route fully covers the requested workflow.",
            })
        ])
        decision = await SemanticRouter(client).decide(
            request="Build a veterinary workflow with treatment plans.",
            domain="software operations",
            routes={"software_build": "build a governed software project"},
        )
        self.assertTrue(decision.domain_match)
        self.assertFalse(decision.known)
        self.assertIsNone(decision.route_id)

    async def test_model_can_reject_out_of_domain_business_work(self):
        client = FakeClient([
            json.dumps({
                "domainMatch": False,
                "known": False,
                "route": None,
                "reason": "This is CRM retrieval rather than software construction.",
            })
        ])
        decision = await SemanticRouter(client).decide(
            request="Show me my open sales leads.",
            domain="software operations",
            routes={"software_build": "build a governed software project"},
        )
        self.assertFalse(decision.domain_match)
        self.assertFalse(decision.known)

    async def test_invalid_route_is_repaired_by_model_not_by_heuristics(self):
        client = FakeClient([
            json.dumps({
                "domainMatch": True,
                "known": True,
                "route": "invented_capability",
                "reason": "Bad first response.",
            }),
            json.dumps({
                "domainMatch": True,
                "known": False,
                "route": None,
                "reason": "No supplied capability fully covers the request.",
            }),
        ])
        decision = await SemanticRouter(client).decide(
            request="Build something outside the bounded capabilities.",
            domain="software operations",
            routes={"software_build": "build a governed software project"},
        )
        self.assertFalse(decision.known)
        self.assertEqual(client.calls, 2)

    async def test_malformed_decision_fails_closed_after_model_repair(self):
        client = FakeClient(["not json", "still not json"])
        with self.assertRaises(SemanticRoutingError):
            await SemanticRouter(client).decide(
                request="Build a portal",
                domain="software operations",
                routes={"software_build": "build a governed software project"},
            )
        self.assertEqual(client.calls, 2)


if __name__ == "__main__":
    unittest.main()
