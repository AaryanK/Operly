import unittest
from pathlib import Path

from packages.database.capability_binding_models import CapabilityBindingRecord
from packages.database.db import Base
from packages.database.digital_event_models import (
    DigitalEventDeliveryRecord,
    DigitalWebhookEndpointRecord,
    DigitalWebhookReceiptRecord,
)
from packages.database.digital_job_models import DigitalPlatformJobRecord
from packages.database.digital_usage_models import DigitalUsageBucketRecord, DigitalUsageLedgerRecord
from packages.database.plugin_credential_models import (
    PluginCredentialBindingRecord,
    PluginEgressGrantRecord,
)
from packages.database.plugin_platform_models import (
    DigitalEventOutboxRecord,
    DigitalResourceBudgetRecord,
    PluginInstallationRecord,
    PluginRuntimeIdentityRecord,
    PluginRuntimeInstanceRecord,
    PluginVersionRecord,
)
from packages.database.plugin_storage_models import PluginKVRecord
from packages.database.schema import ALEMBIC_HEAD, import_all_models
from packages.kernel.contracts import CapabilityRisk
from packages.plugins.contracts import PluginContractError, PluginManifest
from packages.plugins.egress_router import _safe_headers
from packages.plugins.event_router import _validate_target_url
from packages.plugins.jobs import DigitalPlatformJobService
from packages.plugins.runtime_profiles import default_runtime_profiles
from packages.plugins.runtime_provider import validate_remote_base_url
from packages.plugins.worker import DEFAULT_HANDLERS, _event_matches


class PluginPlatformContractTests(unittest.TestCase):
    def manifest(self):
        return {
            "schema_version": "operly.plugin/v1",
            "plugin_id": "acme.billing",
            "version": "1.2.3",
            "display_name": "Acme Billing",
            "description": "Expose Acme billing operations through governed Operly capabilities.",
            "execution_mode": "remote_http",
            "permissions": ["finance:read"],
            "runtime": {
                "profile": "remote-http",
                "kind": "remote",
                "network": {"mode": "egress", "allowed_hosts": ["api.acme.example"]},
                "resources": {
                    "cpu_millicores": 100,
                    "memory_mb": 128,
                    "disk_mb": 64,
                    "max_runtime_seconds": 120,
                    "max_concurrency": 5,
                },
            },
            "capabilities": [
                {
                    "id": "acme.invoice.get",
                    "display_name": "Get Acme invoice",
                    "description": "Read one invoice from the configured Acme account.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"invoice_id": {"type": "string"}},
                        "required": ["invoice_id"],
                        "additionalProperties": False,
                    },
                    "output_schema": {
                        "type": "object",
                        "properties": {"invoice": {"type": "object"}},
                        "required": ["invoice"],
                    },
                    "permissions": ["finance:read"],
                    "risk": "read_only",
                    "tags": ["finance", "billing"],
                }
            ],
            "storage": [{"name": "cache", "kind": "kv", "quota_bytes": 1048576}],
            "credentials": [
                {
                    "name": "acme_api",
                    "credential_type": "api_key",
                    "required": True,
                    "scopes": ["invoice:read"],
                    "allowed_hosts": ["api.acme.example"],
                    "description": "Acme API credential handled by the Operly broker.",
                }
            ],
            "produces_events": [
                {
                    "name": "acme.invoice.changed",
                    "description": "Invoice changed in Acme.",
                    "schema": {"type": "object"},
                }
            ],
            "consumes_events": ["workspace.customer.*"],
            "requested_bindings": [
                {
                    "semantic_name": "customer_email_send",
                    "capability_query": "send customer email",
                    "required": False,
                }
            ],
        }

    def test_manifest_projects_to_kernel_without_new_authority(self):
        manifest = PluginManifest.from_dict(self.manifest())
        spec = manifest.capability_specs()[0]
        self.assertEqual(spec.id, "acme.invoice.get")
        self.assertEqual(spec.provider_id, "operly.plugin_runtime")
        self.assertEqual(spec.scopes, frozenset({"workspace"}))
        self.assertEqual(spec.resource_scope, "workspace")
        self.assertEqual(spec.permissions, ("finance:read",))
        self.assertEqual(spec.risk, CapabilityRisk.READ_ONLY)
        self.assertIn("plugin", spec.tags)
        self.assertEqual(manifest.runtime.network.allowed_hosts, ("api.acme.example",))
        self.assertEqual(manifest.credentials[0].name, "acme_api")
        self.assertEqual(manifest.credentials[0].allowed_hosts, ("api.acme.example",))
        self.assertEqual(manifest.consumes_events, ("workspace.customer.*",))

    def test_non_native_plugin_requires_trusted_runtime_profile(self):
        payload = self.manifest()
        payload.pop("runtime")
        with self.assertRaises(PluginContractError):
            PluginManifest.from_dict(payload)

    def test_network_allowlist_rejects_urls(self):
        payload = self.manifest()
        payload["runtime"]["network"]["allowed_hosts"] = ["https://api.acme.example/v1"]
        with self.assertRaises(PluginContractError):
            PluginManifest.from_dict(payload)

    def test_credential_allowlist_rejects_urls(self):
        payload = self.manifest()
        payload["credentials"][0]["allowed_hosts"] = ["https://api.acme.example"]
        with self.assertRaises(PluginContractError):
            PluginManifest.from_dict(payload)

    def test_runtime_profiles_cover_core_digital_workload_shapes(self):
        registry = default_runtime_profiles()
        ids = {profile.id for profile in registry.all()}
        self.assertTrue(
            {
                "static-web",
                "react-vite",
                "node-web",
                "python-fastapi",
                "worker",
                "sandbox-job",
                "remote-http",
            }
            <= ids
        )
        self.assertTrue(registry.get("react-vite").supports_deploy)
        self.assertEqual(registry.get("sandbox-job").default_network.mode, "off")

    def test_durable_schema_has_security_and_runtime_layers(self):
        self.assertIn("manifest_digest", PluginVersionRecord.__table__.columns)
        self.assertIn("granted_permissions_json", PluginInstallationRecord.__table__.columns)
        self.assertIn("health_state", PluginRuntimeInstanceRecord.__table__.columns)
        self.assertIn("token_hash", PluginRuntimeIdentityRecord.__table__.columns)
        self.assertNotIn("token", PluginRuntimeIdentityRecord.__table__.columns)
        self.assertIn("lease_expires_at", DigitalEventOutboxRecord.__table__.columns)
        self.assertIn("hard_limit", DigitalResourceBudgetRecord.__table__.columns)
        self.assertIn("namespace_id", PluginKVRecord.__table__.columns)
        self.assertIn("authority_user_id", CapabilityBindingRecord.__table__.columns)
        self.assertIn("secret_reference", PluginCredentialBindingRecord.__table__.columns)
        self.assertNotIn("secret", PluginCredentialBindingRecord.__table__.columns)
        self.assertIn("host", PluginEgressGrantRecord.__table__.columns)
        self.assertIn("endpoint_key_hash", DigitalWebhookEndpointRecord.__table__.columns)
        self.assertNotIn("endpoint_key", DigitalWebhookEndpointRecord.__table__.columns)
        self.assertIn("dedupe_key", DigitalWebhookReceiptRecord.__table__.columns)
        self.assertIn("subscription_id", DigitalEventDeliveryRecord.__table__.columns)
        self.assertIn("idempotency_scope", DigitalPlatformJobRecord.__table__.columns)
        self.assertIn("lease_expires_at", DigitalPlatformJobRecord.__table__.columns)
        self.assertIn("window_start", DigitalUsageBucketRecord.__table__.columns)
        self.assertIn("reference_id", DigitalUsageLedgerRecord.__table__.columns)

    def test_all_digital_models_are_registered_and_schema_head_is_current(self):
        import_all_models()
        expected = {
            "plugin_packages",
            "plugin_versions",
            "plugin_installations",
            "plugin_runtime_instances",
            "plugin_runtime_identities",
            "plugin_storage_namespaces",
            "plugin_kv_records",
            "plugin_blob_references",
            "plugin_credential_bindings",
            "plugin_egress_grants",
            "capability_bindings",
            "digital_event_outbox",
            "digital_event_subscriptions",
            "digital_event_deliveries",
            "digital_webhook_endpoints",
            "digital_webhook_receipts",
            "digital_platform_jobs",
            "digital_resource_budgets",
            "digital_usage_buckets",
            "digital_usage_ledger",
        }
        self.assertTrue(expected <= set(Base.metadata.tables))
        self.assertEqual(ALEMBIC_HEAD, "0058_agent_chat_history")

    def test_job_idempotency_scope_never_relies_on_nullable_tenant(self):
        service = DigitalPlatformJobService()
        self.assertEqual(service._scope(None), "platform")
        self.assertEqual(service._scope("workspace-123"), "workspace:workspace-123")

    def test_event_subscription_patterns_are_conservative(self):
        self.assertTrue(_event_matches("*", "plugin.installed"))
        self.assertTrue(_event_matches("plugin.*", "plugin.installed"))
        self.assertTrue(_event_matches("plugin.installed", "plugin.installed"))
        self.assertFalse(_event_matches("plugin.*", "workflow.started"))
        self.assertFalse(_event_matches("plugin.installed", "plugin.disabled"))

    def test_remote_runtime_endpoint_is_https_and_not_local(self):
        endpoint, host, port = validate_remote_base_url("https://api.acme.example")
        self.assertEqual(endpoint, "https://api.acme.example")
        self.assertEqual(host, "api.acme.example")
        self.assertEqual(port, 443)
        for invalid in (
            "http://api.acme.example",
            "https://localhost",
            "https://service.internal",
            "https://user:pass@api.acme.example",  # pragma: allowlist secret
            "https://api.acme.example?token=nope",
        ):
            with self.assertRaises(ValueError, msg=invalid):
                validate_remote_base_url(invalid)

    def test_runtime_egress_runtime_cannot_supply_credentials_or_transport_headers(self):
        self.assertEqual(_safe_headers({"Accept": "application/json"}), {"Accept": "application/json"})
        for name in ("Authorization", "Cookie", "Host", "Content-Length", "X-Operly-Token"):
            with self.assertRaises(PermissionError, msg=name):
                _safe_headers({name: "attacker-value"})

    def test_event_delivery_target_management_rejects_local_or_plain_http(self):
        self.assertEqual(
            _validate_target_url("https://hooks.example.com/operly"),
            "https://hooks.example.com/operly",
        )
        for invalid in (
            "http://hooks.example.com/operly",
            "https://localhost/hook",
            "https://worker.internal/hook",
            "https://user:password@hooks.example.com/hook",  # pragma: allowlist secret
        ):
            with self.assertRaises(ValueError, msg=invalid):
                _validate_target_url(invalid)

    def test_workspace_runtime_composition_is_dynamic_without_loading_connectors(self):
        root = Path(__file__).resolve().parents[1]
        runtime_source = (
            root / "packages" / "workspace_modules" / "tools" / "runtime.py"
        ).read_text(encoding="utf-8")
        self.assertIn("installed_plugin_capability_source", runtime_source)
        self.assertIn("PLUGIN_RUNTIME_PROVIDER_ID", runtime_source)
        self.assertIn("runtime.providers.register(", runtime_source)
        self.assertIn("SandboxJobPluginRuntimeProvider", runtime_source)
        self.assertNotIn("acme.invoice.get", runtime_source)

    def test_platform_worker_owns_runtime_reconciliation(self):
        self.assertIn("plugin.validate", DEFAULT_HANDLERS)
        self.assertIn("plugin.isolated_validate", DEFAULT_HANDLERS)
        self.assertIn("plugin.runtime.reconcile", DEFAULT_HANDLERS)

    def test_api_mounts_runtime_and_broker_transports(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "apps" / "api" / "main.py").read_text(encoding="utf-8")
        workspace_runtime = (
            root / "packages" / "workspace_modules" / "tools" / "runtime.py"
        ).read_text(encoding="utf-8")
        self.assertIn("plugin_runtime_management_router", main)
        self.assertIn("runtime_egress_router", main)
        self.assertIn("plugin_event_router", main)
        self.assertIn("installed_plugin_capability_source", workspace_runtime)
        self.assertIn("PluginRuntimeProvider", workspace_runtime)


if __name__ == "__main__":
    unittest.main()
