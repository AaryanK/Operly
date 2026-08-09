import json
import unittest

from packages.application_builder.routing import route_application_request
from packages.application_builder.schema import ApplicationManifest, BuilderContext, ProposalRequest
from packages.application_builder.service import plan_request
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
                "route": "secure_login",
                "reason": "The request is fully covered by the existing authentication capability.",
            })
        ])
        decision = await route_application_request(
            "Give staff a protected sign-in experience before they can use the portal.",
            client=client,
            context={"surface": "test"},
        )
        self.assertTrue(decision.domain_match)
        self.assertTrue(decision.known)
        self.assertEqual(decision.route_id, "secure_login")
        self.assertEqual(client.calls, 1)

        request = ProposalRequest(
            message="Give staff a protected sign-in experience before they can use the portal.",
            context=BuilderContext(
                workspaceId="t",
                applicationId="a",
                activeVersionId="v",
                selectionScope="application",
                userRole="owner",
            ),
        )
        plan = plan_request(
            request,
            ApplicationManifest(application={"id": "a", "name": "A"}),
            routed_intent=decision.route_id,
        )
        self.assertTrue(any(page["id"] == "login" for page in plan["after"]["pages"]))

    async def test_model_can_mark_in_domain_request_unknown_for_synthesis(self):
        client = FakeClient([
            json.dumps({
                "domainMatch": True,
                "known": False,
                "route": None,
                "reason": "The requested veterinary workflow is not fully covered by an existing capability.",
            })
        ])
        decision = await route_application_request(
            "Build a veterinary appointment system with treatment plans.",
            client=client,
        )
        self.assertTrue(decision.domain_match)
        self.assertFalse(decision.known)
        self.assertIsNone(decision.route_id)

    async def test_model_can_reject_other_business_work_as_outside_builder_domain(self):
        client = FakeClient([
            json.dumps({
                "domainMatch": False,
                "known": False,
                "route": None,
                "reason": "This is a business task rather than an application-building request.",
            })
        ])
        decision = await route_application_request(
            "Show me my open sales leads.",
            client=client,
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
            domain="application building",
            routes={"secure_login": "standard login"},
        )
        self.assertFalse(decision.known)
        self.assertEqual(client.calls, 2)

    async def test_malformed_decision_fails_closed_after_model_repair(self):
        client = FakeClient(["not json", "still not json"])
        with self.assertRaises(SemanticRoutingError):
            await SemanticRouter(client).decide(
                request="Build a portal",
                domain="application building",
                routes={"secure_login": "standard login"},
            )
        self.assertEqual(client.calls, 2)


if __name__ == "__main__":
    unittest.main()
