import unittest

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
from packages.plugins.jobs import DigitalPlatformJobService
from packages.plugins.runtime_profiles import default_runtime_profiles
from packages.plugins.worker import _event_matches


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
        self.assertEqual(ALEMBIC_HEAD, "0055_platform_job_idempotency_scope")

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


if __name__ == "__main__":
    unittest.main()
