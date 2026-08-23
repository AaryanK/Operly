import json
import unittest

from pydantic import ValidationError

from packages.coding_harness.runtime_resolution import RuntimeResolutionError, validate_source_files
from packages.custom_software.source_bundles import SourceFile
from packages.runtime_plugins.fullstack_contract import (
    FULLSTACK_EXECUTION_ENABLED,
    FULLSTACK_RUNTIME_ID,
    FullStackSolutionManifest,
    parse_fullstack_manifest,
    validate_fullstack_source,
)


def _manifest(**overrides):
    value = {
        "schemaVersion": "operly.solution/v1",
        "runtime": FULLSTACK_RUNTIME_ID,
        "dependencies": [],
        "bindings": [
            {
                "semanticName": "customer.notify",
                "capabilityId": "notifications.send",
                "required": True,
            }
        ],
    }
    value.update(overrides)
    return value


def _source(manifest=None, extra=()):
    files = [
        SourceFile("operly.solution.json", json.dumps(manifest or _manifest()).encode(), "test"),
        SourceFile("frontend/index.html", b"<main>Operly</main>", "test"),
        SourceFile("backend/app.py", b"def health(): return True\n", "test"),
        SourceFile("tests/test_health.py", b"def test_health(): assert True\n", "test"),
    ]
    files.extend(extra)
    return files


class FullStackManifestTests(unittest.TestCase):
    def test_valid_contract_uses_canonical_layout_and_capability_bindings(self):
        parsed = parse_fullstack_manifest(_source())
        self.assertEqual(parsed.runtime, "operly-fullstack-v1")
        self.assertEqual(parsed.layout.frontend, "frontend")
        self.assertEqual(parsed.layout.backend, "backend")
        self.assertEqual(parsed.bindings[0].capabilityId, "notifications.send")
        self.assertFalse(FULLSTACK_EXECUTION_ENABLED)
        validation = validate_fullstack_source(_source())
        self.assertTrue(validation.valid, validation.errors)

    def test_runtime_resolver_recognizes_contract_but_does_not_fake_execution(self):
        with self.assertRaises(RuntimeResolutionError) as context:
            validate_source_files(_source())
        message = str(context.exception)
        self.assertIn("operly-fullstack-v1 project contract is valid", message)
        self.assertIn("preview/deploy remain unavailable", message)

    def test_contract_rejects_noncanonical_or_traversing_layout(self):
        with self.assertRaises(ValidationError):
            FullStackSolutionManifest.model_validate(
                _manifest(
                    layout={
                        "frontend": "ui",
                        "backend": "../api",
                        "workers": "workers",
                        "tests": "tests",
                        "migrations": "migrations",
                    }
                )
            )

    def test_contract_rejects_provider_credentials_and_duplicate_bindings(self):
        with self.assertRaises(ValidationError):
            FullStackSolutionManifest.model_validate(
                _manifest(
                    bindings=[
                        {
                            "semanticName": "customer.notify",
                            "capabilityId": "notifications.send",
                            "required": True,
                            "apiKey": "must-never-enter-generated-source",
                        }
                    ]
                )
            )
        with self.assertRaises(ValidationError):
            FullStackSolutionManifest.model_validate(
                _manifest(
                    bindings=[
                        {"semanticName": "mail.send", "capabilityId": "gmail.send", "required": True},
                        {"semanticName": "mail.send", "capabilityId": "notifications.send", "required": False},
                    ]
                )
            )

    def test_dependency_requests_require_lockfiles(self):
        manifest = _manifest(
            dependencies=[
                {"ecosystem": "npm", "name": "react", "version": "19.0.0"},
                {"ecosystem": "python", "name": "fastapi", "version": "0.116.1"},
            ]
        )
        missing = validate_fullstack_source(_source(manifest))
        self.assertFalse(missing.valid)
        self.assertIn("npm dependencies require frontend/package.json and frontend/package-lock.json", missing.errors)
        self.assertIn("Python dependencies require backend/requirements.lock", missing.errors)

        locked = _source(
            manifest,
            [
                SourceFile("frontend/package.json", b"{}", "test"),
                SourceFile("frontend/package-lock.json", b"{}", "test"),
                SourceFile("backend/requirements.lock", b"fastapi==0.116.1\n", "test"),
            ],
        )
        self.assertTrue(validate_fullstack_source(locked).valid)

    def test_source_boundary_rejects_secrets_and_undeclared_roots(self):
        validation = validate_fullstack_source(
            _source(
                extra=[
                    SourceFile("backend/.env", b"TOKEN=secret", "test"),
                    SourceFile("random/sidecar.py", b"print('bypass')", "test"),
                ]
            )
        )
        self.assertFalse(validation.valid)
        self.assertTrue(any("secret file is forbidden" in error for error in validation.errors))
        self.assertTrue(any("declared full-stack layout" in error for error in validation.errors))

    def test_missing_manifest_or_required_application_layers_rejected(self):
        self.assertFalse(validate_fullstack_source([SourceFile("frontend/index.html", b"x", "test")]).valid)
        only_manifest = [SourceFile("operly.solution.json", json.dumps(_manifest()).encode(), "test")]
        validation = validate_fullstack_source(only_manifest)
        self.assertFalse(validation.valid)
        self.assertTrue(any("frontend/" in error for error in validation.errors))
        self.assertTrue(any("backend/" in error for error in validation.errors))
        self.assertTrue(any("tests/" in error for error in validation.errors))


if __name__ == "__main__":
    unittest.main()
