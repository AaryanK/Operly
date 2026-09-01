import unittest

from packages.database.plugin_platform_models import (
    DigitalEventOutboxRecord,
    DigitalResourceBudgetRecord,
    PluginInstallationRecord,
    PluginRuntimeIdentityRecord,
    PluginRuntimeInstanceRecord,
    PluginVersionRecord,
)
from packages.database.plugin_storage_models import PluginKVRecord
from packages.kernel.contracts import CapabilityRisk
from packages.plugins.contracts import PluginContractError, PluginManifest
from packages.plugins.runtime_profiles import default_runtime_profiles


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

    def test_durable_schema_has_package_runtime_identity_event_storage_and_budget_layers(self):
        self.assertIn("manifest_digest", PluginVersionRecord.__table__.columns)
        self.assertIn("granted_permissions_json", PluginInstallationRecord.__table__.columns)
        self.assertIn("health_state", PluginRuntimeInstanceRecord.__table__.columns)
        self.assertIn("token_hash", PluginRuntimeIdentityRecord.__table__.columns)
        self.assertNotIn("token", PluginRuntimeIdentityRecord.__table__.columns)
        self.assertIn("lease_expires_at", DigitalEventOutboxRecord.__table__.columns)
        self.assertIn("hard_limit", DigitalResourceBudgetRecord.__table__.columns)
        self.assertIn("namespace_id", PluginKVRecord.__table__.columns)


if __name__ == "__main__":
    unittest.main()
