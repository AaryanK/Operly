import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from packages.software_projects.coding.build_service import RunnerProfileUnsupported, _check_runner_profile
from packages.runtime_plugins.runtime_resolution import validate_source_files
from packages.runtime_plugins.fullstack_subprocess_runner import FullStackSubprocessTestRunner
from packages.runtime_plugins.runner_contracts import BuildSubmission, NetworkPolicy
from packages.software_projects.source_bundle import SourceFile, build_bundle
from packages.runtime_plugins import register_builtin_runtimes
from packages.runtime_plugins.fullstack_contract import (
    FULLSTACK_EXECUTION_ENABLED,
    FULLSTACK_PROFILE_VERSION,
    FULLSTACK_RUNTIME_ID,
    FullStackSolutionManifest,
    parse_fullstack_manifest,
    validate_fullstack_source,
)


def _manifest(**overrides):
    value = {
        "schemaVersion": "operly.solution/v1",
        "runtime": FULLSTACK_RUNTIME_ID,
        "runtimeVersion": FULLSTACK_PROFILE_VERSION,
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
        SourceFile("tests/test_health.py", b"import unittest\nclass HealthTest(unittest.TestCase):\n    def test_health(self): self.assertTrue(True)\n", "test"),
    ]
    files.extend(extra)
    return files


def _npm_lock(*, resolved="https://registry.npmjs.org/react/-/react-19.0.0.tgz"):
    return {
        "name": "operly-generated",
        "version": "1.0.0",
        "lockfileVersion": 3,
        "requires": True,
        "packages": {
            "": {"dependencies": {"react": "19.0.0"}},
            "node_modules/react": {
                "version": "19.0.0",
                "resolved": resolved,
                "integrity": "sha512-YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXpBQ0RFRkdISUpLTE1OT1BRUlNUVVZXWFlaYWJjZGVmZw==",
            },
        },
    }


class FullStackManifestTests(unittest.TestCase):
    def test_valid_contract_uses_canonical_layout_and_capability_bindings(self):
        parsed = parse_fullstack_manifest(_source())
        self.assertEqual(parsed.runtime, "operly-fullstack-v1")
        self.assertEqual(parsed.runtimeVersion, 1)
        self.assertEqual(parsed.layout.frontend, "frontend")
        self.assertEqual(parsed.layout.backend, "backend")
        self.assertEqual(parsed.bindings[0].capabilityId, "notifications.send")
        self.assertTrue(FULLSTACK_EXECUTION_ENABLED)
        validation = validate_fullstack_source(_source())
        self.assertTrue(validation.valid, validation.errors)

    def test_runtime_resolver_selects_fullstack_plugin(self):
        self.assertEqual(validate_source_files(_source()), FULLSTACK_RUNTIME_ID)
        plugin = register_builtin_runtimes().get(FULLSTACK_RUNTIME_ID)
        self.assertEqual(plugin.spec.version, "1")
        self.assertTrue(plugin.spec.supports_preview)
        self.assertFalse(plugin.spec.supports_deploy)
        self.assertEqual(plugin.spec.service_binding_modes, frozenset({"capability_gateway"}))

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

    def test_dependency_requests_require_execution_mode_and_lockfiles(self):
        with self.assertRaises(ValidationError):
            FullStackSolutionManifest.model_validate(
                _manifest(
                    dependencies=[{"ecosystem": "npm", "name": "react", "version": "19.0.0"}]
                )
            )

        manifest = _manifest(
            execution={"frontend": "npm-build", "backend": "python-cli", "worker": "none", "healthPath": "/health"},
            dependencies=[
                {"ecosystem": "npm", "name": "react", "version": "19.0.0"},
                {"ecosystem": "python", "name": "fastapi", "version": "0.116.1"},
            ],
        )
        missing = validate_fullstack_source(_source(manifest))
        self.assertFalse(missing.valid)
        self.assertIn("npm-build frontend requires frontend/package.json and frontend/package-lock.json", missing.errors)
        self.assertIn("Python dependencies require backend/requirements.lock", missing.errors)

        package = {
            "scripts": {"build": "node build.js"},
            "dependencies": {"react": "19.0.0"},
        }
        locked = _source(
            manifest,
            [
                SourceFile("frontend/package.json", json.dumps(package).encode(), "test"),
                SourceFile("frontend/package-lock.json", json.dumps(_npm_lock()).encode(), "test"),
                SourceFile("frontend/build.js", b"console.log('build')\n", "test"),
                SourceFile("backend/requirements.lock", b"fastapi==0.116.1\n", "test"),
            ],
        )
        validation = validate_fullstack_source(locked)
        self.assertTrue(validation.valid, validation.errors)

    def test_dependency_lockfiles_cannot_escape_approved_registries(self):
        python_manifest = _manifest(
            dependencies=[{"ecosystem": "python", "name": "fastapi", "version": "0.116.1"}]
        )
        python_escape = validate_fullstack_source(
            _source(
                python_manifest,
                [
                    SourceFile(
                        "backend/requirements.lock",
                        b"fastapi @ git+https://evil.example/fastapi.git\n",
                        "test",
                    )
                ],
            )
        )
        self.assertFalse(python_escape.valid)
        self.assertTrue(any("URLs, options and editable installs are forbidden" in item for item in python_escape.errors))

        npm_manifest = _manifest(
            execution={"frontend": "npm-build", "backend": "python-cli", "worker": "none", "healthPath": "/health"},
            dependencies=[{"ecosystem": "npm", "name": "react", "version": "19.0.0"}],
        )
        package = {"scripts": {"build": "node build.js"}, "dependencies": {"react": "19.0.0"}}
        npm_escape = validate_fullstack_source(
            _source(
                npm_manifest,
                [
                    SourceFile("frontend/package.json", json.dumps(package).encode(), "test"),
                    SourceFile(
                        "frontend/package-lock.json",
                        json.dumps(_npm_lock(resolved="https://evil.example/react.tgz")).encode(),
                        "test",
                    ),
                    SourceFile("frontend/build.js", b"console.log('build')\n", "test"),
                ],
            )
        )
        self.assertFalse(npm_escape.valid)
        self.assertTrue(any("registry.npmjs.org" in item for item in npm_escape.errors))

    def test_worker_entrypoint_and_backend_entrypoint_are_fixed_contracts(self):
        missing_backend = [item for item in _source() if item.path != "backend/app.py"]
        validation = validate_fullstack_source(missing_backend)
        self.assertFalse(validation.valid)
        self.assertIn("operly-fullstack-v1 requires backend/app.py as the trusted Python CLI entrypoint", validation.errors)

        worker_manifest = _manifest(
            execution={"frontend": "static", "backend": "python-cli", "worker": "python-cli", "healthPath": "/health"}
        )
        validation = validate_fullstack_source(_source(worker_manifest))
        self.assertFalse(validation.valid)
        self.assertIn("execution.worker=python-cli requires workers/worker.py", validation.errors)

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

    def test_runtime_plugin_builds_registry_bounded_submission_and_semantic_bindings(self):
        manifest = _manifest(
            dependencies=[{"ecosystem": "python", "name": "fastapi", "version": "0.116.1"}]
        )
        files = _source(
            manifest,
            [SourceFile("backend/requirements.lock", b"fastapi==0.116.1\n", "test")],
        )
        bundle = build_bundle(
            files,
            "workspace-1",
            "application-1",
            "plan-1",
            1,
            1,
            "sha256:" + "0" * 64,
        )
        record = SimpleNamespace(
            tenant_id="workspace-1",
            application_id="application-1",
            plan_version=1,
            source_version=1,
            bundle_digest=bundle.digest,
        )
        submission = register_builtin_runtimes().get(FULLSTACK_RUNTIME_ID).build_submission_from_record(
            record, bundle, "fullstack-submission"
        )
        self.assertEqual(submission.stackId, FULLSTACK_RUNTIME_ID)
        self.assertEqual(submission.stackVersion, FULLSTACK_PROFILE_VERSION)
        self.assertEqual(submission.installNetwork.mode, "dependency_registry_only")
        self.assertEqual(submission.network.mode, "loopback_only")
        self.assertEqual(submission.dependencies[0].ecosystem, "python")
        self.assertEqual(submission.dependencies[0].registry, "pypi")
        self.assertEqual(submission.serviceBindings[0].semanticName, "customer.notify")
        self.assertEqual(submission.serviceBindings[0].capabilityId, "notifications.send")
        dumped = submission.model_dump(mode="json")
        self.assertNotIn("apiKey", json.dumps(dumped))
        self.assertFalse(submission.secretAliases)

    def test_dependency_submissions_fail_closed_without_install_network(self):
        with self.assertRaises(ValidationError):
            BuildSubmission(
                workspaceId="w",
                applicationId="a",
                planVersion=1,
                sourceVersion=1,
                stackId=FULLSTACK_RUNTIME_ID,
                stackVersion=1,
                sourceBundleDigest="sha256:" + "a" * 64,
                dependencies=[{"ecosystem": "python", "name": "fastapi", "version": "0.116.1", "registry": "pypi"}],
                operations=["resolve_dependencies", "build"],
                healthCheck={"path": "/health"},
                installNetwork=NetworkPolicy(mode="none"),
                idempotencyKey="abcdefgh",
            )


class _CapabilityAdapter:
    def __init__(self, version):
        self.version = version

    async def capabilities(self):
        return {
            "protocolVersion": 2,
            "profiles": {FULLSTACK_RUNTIME_ID: {"profileVersion": self.version}},
        }


class FullStackProfileNegotiationTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_runner_profile_version_is_required(self):
        await _check_runner_profile(_CapabilityAdapter(1), FULLSTACK_RUNTIME_ID, 1)
        with self.assertRaises(RunnerProfileUnsupported):
            await _check_runner_profile(_CapabilityAdapter(2), FULLSTACK_RUNTIME_ID, 1)


_BACKEND_APP = b'''import argparse\nfrom http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n\ndef body_for(path):\n    if path == "/health":\n        return 200, b"ok"\n    if path == "/":\n        return 200, b"operly-fullstack-reference"\n    return 404, b"not-found"\n\nclass Handler(BaseHTTPRequestHandler):\n    def do_GET(self):\n        status, body = body_for(self.path.split("?", 1)[0])\n        self.send_response(status)\n        self.end_headers()\n        self.wfile.write(body)\n    def log_message(self, *args):\n        return\n\ndef main():\n    parser = argparse.ArgumentParser()\n    parser.add_argument("--host", default="127.0.0.1")\n    parser.add_argument("--port", type=int, default=8080)\n    args = parser.parse_args()\n    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()\n\nif __name__ == "__main__":\n    main()\n'''

_BACKEND_TEST = b'''import unittest\nfrom backend.app import body_for\n\nclass BackendTest(unittest.TestCase):\n    def test_health_and_root(self):\n        self.assertEqual(body_for("/health"), (200, b"ok"))\n        self.assertEqual(body_for("/")[0], 200)\n'''


class FullStackReferenceExecutorTests(unittest.IsolatedAsyncioTestCase):
    def _bundle_and_submission(self):
        files = [
            SourceFile("operly.solution.json", json.dumps(_manifest()).encode(), "test"),
            SourceFile("frontend/index.html", b"<main>Reference</main>", "test"),
            SourceFile("backend/app.py", _BACKEND_APP, "test"),
            SourceFile("workers/README.md", b"No worker requested.\n", "test"),
            SourceFile("migrations/README.md", b"No migrations yet.\n", "test"),
            SourceFile("tests/test_backend.py", _BACKEND_TEST, "test"),
        ]
        bundle = build_bundle(
            files,
            "workspace-ref",
            "application-ref",
            "plan-ref",
            1,
            1,
            "sha256:" + "0" * 64,
        )
        record = SimpleNamespace(
            tenant_id="workspace-ref",
            application_id="application-ref",
            plan_version=1,
            source_version=1,
            bundle_digest=bundle.digest,
        )
        submission = register_builtin_runtimes().get(FULLSTACK_RUNTIME_ID).build_submission_from_record(
            record, bundle, "fullstack-reference"
        )
        return bundle, submission

    async def test_reference_executor_runs_full_quality_gate_and_cleans_up(self):
        bundle, submission = self._bundle_and_submission()
        with patch.dict(
            os.environ,
            {"OPERLY_ENABLE_TEST_SUBPROCESS_RUNNER": "1", "OPERLY_ENV": "test"},
            clear=False,
        ):
            runner = FullStackSubprocessTestRunner()
            response = await runner.submit(submission, bundle)
            self.assertEqual(response["state"], "preview_ready", response)
            result = response["result"]
            self.assertTrue(result["buildSuccess"])
            self.assertTrue(result["testSuccess"])
            self.assertTrue(result["processStartSuccess"])
            self.assertTrue(result["healthCheckSuccess"])
            self.assertTrue(result["acceptanceCheckSuccess"])
            self.assertTrue(result["previewAvailable"])
            await runner.cleanup(response["jobId"])
            self.assertEqual((await runner.status(response["jobId"]))["state"], "cleaned")


if __name__ == "__main__":
    unittest.main()
