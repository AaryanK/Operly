"""No-inference, no-network contract tests for the guarded remote harness bridge."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from apps.api.dragonzpyder_broker_router import _authorize
from packages.agent_runtime import remote_harness as bridge
from packages.kernel.contracts import CapabilityRisk
from packages.security.execution_context import ScopeKind


def context(scope=ScopeKind.WORKSPACE):
    return SimpleNamespace(
        scope_kind=scope,
        is_workspace=scope == ScopeKind.WORKSPACE,
        user_id="account-test",
        workspace_id="workspace-test" if scope == ScopeKind.WORKSPACE else None,
        conversation_id="thread-test",
    )


class DragonZpyderRemoteHarnessTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {
            "OPERLY_DRAGONZPYDER_DELEGATION_KEY": "d" * 48,
            "OPERLY_DRAGONZPYDER_AGENT_TOKEN": "a" * 48,
            "OPERLY_DRAGONZPYDER_BROKER_TOKEN": "b" * 48,
            "OPERLY_DRAGONZPYDER_AGENT_BROKER_ENABLED": "1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_readonly_lease_excludes_mutations(self):
        capabilities = [
            SimpleNamespace(id="calendar.list", risk=CapabilityRisk.READ_ONLY),
            SimpleNamespace(id="gmail.send", risk=CapabilityRisk.HIGH),
        ]
        lease = bridge.issue_readonly_lease(context(), run_id="run-test", capabilities=capabilities)
        claims = bridge.validate_readonly_lease(lease)
        self.assertEqual(claims["capability_ids"], ["calendar.list"])
        self.assertEqual(claims["workspace_id"], "workspace-test")
        self.assertEqual(claims["user_id"], "account-test")

    def test_personal_never_becomes_a_workspace(self):
        lease = bridge.issue_readonly_lease(context(ScopeKind.PERSONAL), run_id="personal", capabilities=[])
        claims = bridge.validate_readonly_lease(lease)
        self.assertEqual(claims["scope_kind"], "personal")
        self.assertIsNone(claims["workspace_id"])

    def test_tampered_and_expired_leases_fail(self):
        lease = bridge.issue_readonly_lease(context(), run_id="run-test", capabilities=[])
        blob, signature = lease.split(".")
        with self.assertRaises(PermissionError):
            bridge.validate_readonly_lease(blob[:-1] + ("A" if blob[-1] != "A" else "B") + "." + signature)
        with patch.object(bridge.time, "time", return_value=bridge.time.time() + 500):
            with self.assertRaises(PermissionError):
                bridge.validate_readonly_lease(lease)

    def test_broker_requires_service_secret_and_flag(self):
        lease = bridge.issue_readonly_lease(context(), run_id="run-test", capabilities=[])
        self.assertEqual(_authorize("Bearer " + "b" * 48, lease)["user_id"], "account-test")
        with self.assertRaises(HTTPException) as bad_token:
            _authorize("Bearer wrong", lease)
        self.assertEqual(bad_token.exception.status_code, 401)
        with patch.dict("os.environ", {"OPERLY_DRAGONZPYDER_AGENT_BROKER_ENABLED": "0"}):
            with self.assertRaises(HTTPException) as disabled:
                _authorize("Bearer " + "b" * 48, lease)
        self.assertEqual(disabled.exception.status_code, 401)

    def test_agent_url_rejects_an_untrusted_origin(self):
        with patch.dict("os.environ", {
            "OPERLY_DRAGONZPYDER_AGENT_URL": "https://evil.example/internal/agents/operly/run",
        }):
            with self.assertRaises(bridge.RemoteHarnessUnavailable):
                bridge.dragonzpyder_endpoint()
        with patch.dict("os.environ", {
            "OPERLY_DRAGONZPYDER_AGENT_URL": "https://api.dragonzpyder.xyz/internal/agents/operly/run",
        }):
            self.assertTrue(bridge.dragonzpyder_endpoint().startswith("https://api.dragonzpyder.xyz/"))


if __name__ == "__main__":
    unittest.main()
