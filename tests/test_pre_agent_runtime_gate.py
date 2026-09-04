import unittest
from pathlib import Path

from packages.kernel.contracts import CapabilityRisk
from packages.workspace_modules.agent_computer.router import _governed_native_contracts


class PreAgentRuntimeSecurityGateTests(unittest.TestCase):
    def test_kernel_rejects_unstable_mutations_before_approval(self):
        source = Path("packages/kernel/runtime.py").read_text(encoding="utf-8")
        request_guard = 'and not str(execution_request.request_id or "").strip()'
        approval_branch = "if decision is AuthorizationDecision.ASK and execution_request.approval_id:"
        self.assertIn(request_guard, source)
        self.assertLess(source.index(request_guard), source.index(approval_branch))

    def test_approval_is_bound_to_original_request_and_claimed_before_provider(self):
        approvals = Path("packages/kernel/approvals.py").read_text(encoding="utf-8")
        idempotency = Path("packages/kernel/idempotency.py").read_text(encoding="utf-8")
        runtime = Path("packages/kernel/runtime.py").read_text(encoding="utf-8")

        self.assertIn('if not original_request_id:', approvals)
        self.assertIn('current_request_id != original_request_id', approvals)
        self.assertIn('row.status = "executing"', approvals)
        self.assertIn('await claim_approved_invocation(', idempotency)
        self.assertIn('await db.commit()', idempotency)

        reserve = runtime.index("reservation = await reserve_request(")
        provider = runtime.index("execution_result = await provider.execute(")
        self.assertLess(reserve, provider)

    def test_idempotency_reservation_is_durable_before_provider_execution(self):
        source = Path("packages/kernel/idempotency.py").read_text(encoding="utf-8")
        claim = source.index("claim = KernelRequestClaim(")
        durable_commit = source.index("await db.commit()", claim)
        self.assertGreater(durable_commit, claim)
        self.assertIn("must survive a provider timeout, process crash", source)

    def test_agent_computer_reports_governed_contracts(self):
        contracts = {spec.id: spec for spec in _governed_native_contracts()}
        for capability_id in (
            "computer.terminal.exec",
            "computer.python.exec",
            "computer.git.exec",
            "computer.browser.evaluate",
        ):
            spec = contracts[capability_id]
            self.assertEqual(spec.risk, CapabilityRisk.HIGH)
            self.assertTrue(spec.approval_required)
            self.assertFalse(spec.reversible)

        router = Path("packages/workspace_modules/agent_computer/router.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("native_contracts = _governed_native_contracts()", router)
        self.assertIn("for contract in _governed_native_contracts():", router)

    def test_agent_computer_mutations_cannot_get_random_retry_ids(self):
        router = Path("packages/workspace_modules/agent_computer/router.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("if spec.risk is not CapabilityRisk.READ_ONLY and not clean_request_id:", router)
        self.assertIn('"code": "request_id_required"', router)
        self.assertIn("agent-computer-read:", router)
        self.assertNotIn(
            'request_id = request_id or f"agent-computer:{row.id}:{uuid4()}"',
            router,
        )

    def test_studio_generated_content_never_gets_same_origin_privilege(self):
        headers = Path("apps/api/security_headers.py").read_text(encoding="utf-8")
        studio = Path("packages/workspace_modules/studio/router.py").read_text(encoding="utf-8")
        for source in (headers, studio):
            csp_start = source.index("PUBLISHED_STUDIO_CSP")
            csp_section = source[csp_start : csp_start + 1400]
            self.assertIn("sandbox allow-scripts", csp_section)
            self.assertNotIn("allow-same-origin", csp_section)

    def test_runner_request_integrity_uses_separate_freshness_bound_secret(self):
        runner = Path("apps/sandbox_runner/server.mjs").read_text(encoding="utf-8")
        client = Path("packages/workspace_modules/agent_computer/sandbox.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("OPERLY_RUNNER_SIGNING_KEY", runner)
        self.assertIn("OPERLY_RUNNER_TOKEN and OPERLY_RUNNER_SIGNING_KEY must be different", runner)
        self.assertIn("runner request replay detected", runner)
        self.assertIn('"X-Operly-Timestamp"', client)
        self.assertIn('"X-Operly-Nonce"', client)
        self.assertIn("OPERLY_SANDBOX_RUNNER_SIGNING_KEY", client)

    def test_ssrf_sensitive_requests_connect_only_to_policy_checked_ip(self):
        egress = Path("packages/plugins/egress_router.py").read_text(encoding="utf-8")
        deliveries = Path("packages/plugins/deliveries.py").read_text(encoding="utf-8")
        for source in (egress, deliveries):
            self.assertIn("resolve_public_addresses", source)
            self.assertIn("pinned_https_url", source)
            self.assertIn("sni_extensions(host)", source)

    def test_control_plane_container_runs_unprivileged(self):
        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertLess(dockerfile.index("USER 10001:10001"), dockerfile.index("CMD ["))


if __name__ == "__main__":
    unittest.main()
