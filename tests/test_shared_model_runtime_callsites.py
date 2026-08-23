import json
import unittest
from unittest.mock import patch

from packages.application_builder.ai import ApplicationBuilderAI
from packages.application_builder.schema import ApplicationManifest, BuilderContext, ProposalRequest
from packages.model_runtime.semantic_router import SemanticRouter


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def chat(self, messages, tools):
        self.calls += 1
        return {"content": self.responses.pop(0)}


class SharedModelRuntimeCallsiteTests(unittest.IsolatedAsyncioTestCase):
    def _request(self):
        return ProposalRequest(
            message="Build a veterinary appointment system.",
            context=BuilderContext(
                workspaceId="t",
                applicationId="a",
                activeVersionId="v",
                selectionScope="application",
                userRole="owner",
            ),
        )

    async def test_managed_app_synthesis_resolves_planner_role(self):
        response = json.dumps(
            {
                "application": {"id": "a", "name": "Generated Clinic"},
                "modules": [],
                "pages": [],
                "regions": [],
                "components": [],
                "entities": [],
                "permissions": [],
                "workflows": [],
                "integrations": [],
                "routes": [],
            }
        )
        client = FakeClient([response])
        with patch(
            "packages.application_builder.ai.model_chat_client_for_role",
            return_value=client,
        ) as factory:
            plan = await ApplicationBuilderAI().plan(
                self._request(), ApplicationManifest(application={"id": "a", "name": "A"})
            )
        factory.assert_called_once_with("planner")
        self.assertEqual(client.calls, 1)
        self.assertEqual(plan["after"]["application"]["name"], "Generated Clinic")

    async def test_manifest_repair_uses_repair_role(self):
        repaired = json.dumps(
            {
                "application": {"id": "a", "name": "Repaired Clinic"},
                "modules": [],
                "pages": [],
                "regions": [],
                "components": [],
                "entities": [],
                "permissions": [],
                "workflows": [],
                "integrations": [],
                "routes": [],
            }
        )
        planner = FakeClient(["not-json"])
        repair = FakeClient([repaired])

        def client_for_role(role):
            return {"planner": planner, "repair": repair}[role]

        with patch(
            "packages.application_builder.ai.model_chat_client_for_role",
            side_effect=client_for_role,
        ) as factory:
            plan = await ApplicationBuilderAI().plan(
                self._request(), ApplicationManifest(application={"id": "a", "name": "A"})
            )
        self.assertEqual([call.args[0] for call in factory.call_args_list], ["planner", "repair"])
        self.assertEqual(plan["after"]["application"]["name"], "Repaired Clinic")

    async def test_semantic_router_resolves_bounded_task_role(self):
        client = FakeClient(
            [
                json.dumps(
                    {
                        "domainMatch": True,
                        "known": True,
                        "route": "secure_login",
                        "reason": "The bounded capability fully satisfies the request.",
                    }
                )
            ]
        )
        with patch(
            "packages.model_runtime.semantic_router.model_chat_client_for_role",
            return_value=client,
        ) as factory:
            decision = await SemanticRouter().decide(
                request="Give staff a secure sign-in page.",
                domain="application building",
                routes={"secure_login": "standard secure login"},
            )
        factory.assert_called_once_with("bounded_task")
        self.assertTrue(decision.known)
        self.assertEqual(decision.route_id, "secure_login")


if __name__ == "__main__":
    unittest.main()
