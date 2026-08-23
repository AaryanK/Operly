import hashlib
import hmac
import importlib
import json
import os
import tempfile
import time
import unittest
import uuid
from types import SimpleNamespace
from urllib.parse import urlparse
from unittest.mock import patch

from packages.custom_software.source_bundles import SourceFile, build_bundle
from packages.runtime_plugins import FULLSTACK_RUNTIME_ID
from packages.runtime_plugins.fullstack_runtime import FullStackRuntime


_BACKEND_APP = b'''import argparse\nfrom http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n\ndef response_for(path):\n    if path == "/health": return 200, b"ok"\n    if path == "/": return 200, b"isolated-operly-runner"\n    return 404, b"not-found"\n\nclass Handler(BaseHTTPRequestHandler):\n    def do_GET(self):\n        status, body = response_for(self.path.split("?", 1)[0])\n        self.send_response(status)\n        self.end_headers()\n        self.wfile.write(body)\n    def log_message(self, *args): return\n\ndef main():\n    parser = argparse.ArgumentParser()\n    parser.add_argument("--host", default="127.0.0.1")\n    parser.add_argument("--port", type=int, default=8080)\n    args = parser.parse_args()\n    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()\n\nif __name__ == "__main__": main()\n'''

_BACKEND_TEST = b'''import unittest\nfrom backend.app import response_for\nclass AppTest(unittest.TestCase):\n    def test_health(self):\n        self.assertEqual(response_for("/health"), (200, b"ok"))\n'''


def _bundle_and_submission():
    manifest = {
        "schemaVersion": "operly.solution/v1",
        "runtime": "operly-fullstack-v1",
        "runtimeVersion": 1,
        "dependencies": [],
        "bindings": [],
    }
    files = [
        SourceFile("operly.solution.json", json.dumps(manifest).encode(), "test"),
        SourceFile("frontend/index.html", b"<main>isolated</main>", "test"),
        SourceFile("backend/app.py", _BACKEND_APP, "test"),
        SourceFile("tests/test_backend.py", _BACKEND_TEST, "test"),
        SourceFile("workers/README.md", b"No worker requested.\n", "test"),
        SourceFile("migrations/README.md", b"No migrations requested.\n", "test"),
    ]
    bundle = build_bundle(
        files,
        "workspace-isolation",
        "application-isolation",
        "plan-isolation",
        1,
        1,
        "sha256:" + "0" * 64,
    )
    record = SimpleNamespace(
        tenant_id="workspace-isolation",
        application_id="application-isolation",
        plan_version=1,
        source_version=1,
        bundle_digest=bundle.digest,
    )
    runtime = FullStackRuntime()
    self_check = runtime.validate(bundle)
    if not self_check.valid:
        raise AssertionError(self_check.errors)
    submission = runtime.build_submission_from_record(
        record, bundle, "production-isolation-test"
    )
    payload = {
        "submission": submission.model_dump(mode="json"),
        "bundle": {
            "manifest": bundle.manifest,
            "files": [
                {
                    "path": item.path,
                    "content": item.content.decode(),
                    "generatedBy": item.generated_by,
                }
                for item in bundle.files
            ],
        },
    }
    return bundle, submission, payload


class RunnerSidecarPolicyTests(unittest.TestCase):
    def test_registry_proxy_rejects_arbitrary_and_ip_hosts(self):
        from apps.runner import egress_proxy

        self.assertEqual(egress_proxy._safe_hostname("pypi.org"), "pypi.org")
        for value in ("example.com", "127.0.0.1", "169.254.169.254", "10.0.0.1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                egress_proxy._safe_hostname(value)

    def test_registry_dns_must_resolve_publicly(self):
        from apps.runner import egress_proxy

        private = [(2, 1, 6, "", ("10.0.0.4", 443))]
        with patch.object(egress_proxy.socket, "getaddrinfo", return_value=private):
            with self.assertRaises(ValueError):
                egress_proxy._global_addresses("pypi.org", 443)


class RunnerStoreTests(unittest.TestCase):
    def test_idempotency_is_durable(self):
        from apps.runner.store import RunnerStore

        with tempfile.TemporaryDirectory() as root:
            store = RunnerStore(root)
            first = store.create("job-1", "idempotency-key", {"hello": "world"})
            second = store.create("job-2", "idempotency-key", {"hello": "again"})
            self.assertEqual(first.id, "job-1")
            self.assertEqual(second.id, "job-1")
            reopened = RunnerStore(root)
            self.assertEqual(reopened.by_idempotency("idempotency-key").id, "job-1")

    def test_preview_ready_records_are_queryable_for_restart_reconciliation(self):
        from apps.runner.store import RunnerStore

        with tempfile.TemporaryDirectory() as root:
            store = RunnerStore(root)
            store.create("job-preview", "preview-key", {"hello": "world"})
            store.update(
                "job-preview",
                state="preview_ready",
                response={"jobId": "job-preview", "state": "preview_ready"},
                resources={"runtimeContainer": "runtime-1"},
                preview_id="preview-job-preview",
                preview_token="opaque-preview-token",
                preview_upstream="http://172.20.0.3:8082",
            )
            self.assertEqual([item.id for item in store.preview_ready()], ["job-preview"])


@unittest.skipUnless(
    os.getenv("OPERLY_REAL_ISOLATION_RUNNER") == "1",
    "real Docker isolation runner is only exercised in the dedicated CI gate",
)
class ProductionIsolationAcceptance(unittest.TestCase):
    def setUp(self):
        import docker

        self.docker = docker.from_env()
        self.control_network_name = "operly-runner-control-test-" + uuid.uuid4().hex[:10]
        self.control_network = self.docker.networks.create(
            self.control_network_name,
            driver="bridge",
            internal=False,
            labels={"operly.runner.test": "true"},
        )
        self.state = tempfile.TemporaryDirectory()
        self.token = "runner-test-" + "x" * 48
        self.env = patch.dict(
            os.environ,
            {
                "OPERLY_RUNNER_TOKEN": self.token,
                "OPERLY_RUNNER_PUBLIC_BASE_URL": "https://runner.test",
                "OPERLY_RUNNER_STATE_DIR": self.state.name,
                "OPERLY_RUNNER_JOB_IMAGE": os.getenv(
                    "OPERLY_RUNNER_JOB_IMAGE", "operly-runner-job:test"
                ),
                "OPERLY_RUNNER_PROXY_IMAGE": os.getenv(
                    "OPERLY_RUNNER_PROXY_IMAGE", "operly-runner-proxy:test"
                ),
                "OPERLY_RUNNER_CONTROL_NETWORK": self.control_network_name,
                "OPERLY_RUNNER_EGRESS_NETWORK": "bridge",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        try:
            self.control_network.remove()
        except Exception:
            pass
        self.state.cleanup()

    def _headers(self, raw: bytes):
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "X-Operly-Signature": hmac.new(
                self.token.encode(), raw, hashlib.sha256
            ).hexdigest(),
        }

    def _request(self, client, method, path, payload=None):
        raw = json.dumps(payload or {}, sort_keys=True).encode()
        response = client.request(
            method,
            path,
            content=raw,
            headers=self._headers(raw),
        )
        expected = hmac.new(
            self.token.encode(), response.content, hashlib.sha256
        ).hexdigest()
        self.assertEqual(response.headers.get("X-Operly-Signature"), expected)
        return response

    def test_gateway_runs_real_job_in_locked_down_container_and_cleans_it(self):
        from fastapi.testclient import TestClient
        import apps.runner.main as runner_main

        runner_main = importlib.reload(runner_main)
        _bundle, _submission, payload = _bundle_and_submission()

        with TestClient(runner_main.app) as client:
            health = client.get("/health")
            self.assertEqual(health.status_code, 200, health.text)
            self.assertEqual(health.json()["isolation"], "dedicated_host_container_per_job")

            unauthorized = client.get("/v1/capabilities")
            self.assertEqual(unauthorized.status_code, 401)
            expected = hmac.new(
                self.token.encode(), unauthorized.content, hashlib.sha256
            ).hexdigest()
            self.assertEqual(unauthorized.headers.get("X-Operly-Signature"), expected)

            created = self._request(client, "POST", "/v1/builds", payload)
            self.assertEqual(created.status_code, 202, created.text)
            job_id = created.json()["jobId"]

            repeated = self._request(client, "POST", "/v1/builds", payload)
            self.assertEqual(repeated.json()["jobId"], job_id)

            deadline = time.monotonic() + 45
            status = None
            while time.monotonic() < deadline:
                status = self._request(client, "GET", f"/v1/builds/{job_id}").json()
                if status.get("state") in {"preview_ready", "failed", "cancelled"}:
                    break
                time.sleep(0.2)
            self.assertIsNotNone(status)
            self.assertEqual(status.get("state"), "preview_ready", status)
            result = status["result"]
            self.assertTrue(result["buildSuccess"])
            self.assertTrue(result["testSuccess"])
            self.assertTrue(result["healthCheckSuccess"])
            self.assertTrue(result["acceptanceCheckSuccess"])
            self.assertTrue(result["previewAvailable"])

            record = runner_main.store.get(job_id)
            inspection = runner_main.backend.inspect_runtime(record.resources)
            self.assertTrue(inspection["readOnlyRootfs"], inspection)
            self.assertIn("ALL", inspection["capDrop"])
            self.assertEqual(len(inspection["networks"]), 1)
            self.assertTrue(inspection["networks"][0].startswith("operly-job-"))

            runtime = self.docker.containers.get(record.resources["runtimeContainer"])
            runtime.reload()
            socket_check = runtime.exec_run(
                ["test", "!", "-e", "/var/run/docker.sock"],
                user="10001:10001",
            )
            self.assertEqual(socket_check.exit_code, 0)
            self.assertFalse(runtime.attrs.get("Mounts"), runtime.attrs.get("Mounts"))

            direct_egress = runtime.exec_run(
                [
                    "python",
                    "-c",
                    "import socket; s=socket.socket(); s.settimeout(1); s.connect(('1.1.1.1',443))",
                ],
                user="10001:10001",
            )
            self.assertNotEqual(direct_egress.exit_code, 0)

            preview_url = status["preview"]["targetUrl"]
            parsed = urlparse(preview_url)
            preview = client.get(parsed.path)
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertIn("isolated-operly-runner", preview.text)

            cleaned = self._request(client, "POST", f"/v1/builds/{job_id}/cleanup")
            self.assertEqual(cleaned.json()["state"], "cleaned")
            self.assertEqual(runner_main.backend.inspect_runtime(record.resources), {})


if __name__ == "__main__":
    unittest.main()
