from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import urllib.error
import urllib.request
import zipfile

BASE = os.environ["PLUGIN_SMOKE_RUNNER_URL"].rstrip("/")
TOKEN = os.environ["PLUGIN_SMOKE_RUNNER_TOKEN"]


def signed_request(method: str, path: str, payload: dict | None = None, timeout: int = 420) -> dict:
    raw = b"" if payload is None else json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    canonical = method.upper().encode() + b"\n" + path.encode() + b"\n" + raw
    signature = hmac.new(TOKEN.encode(), canonical, hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        BASE + path,
        data=(raw if payload is not None else None),
        method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "X-Operly-Signature": signature,
            "Content-Type": "application/json",
            "User-Agent": "operly-plugin-runner-smoke/1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read()
            supplied = response.headers.get("X-Operly-Signature", "")
            expected = hmac.new(TOKEN.encode(), body, hashlib.sha256).hexdigest()
            if not supplied or not hmac.compare_digest(supplied, expected):
                raise RuntimeError("runner response signature mismatch")
            data = json.loads(body or b"{}")
            if not isinstance(data, dict) or data.get("ok") is False:
                raise RuntimeError(f"runner rejected request: {data}")
            return data
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"runner HTTP {exc.code}: {exc.read().decode(errors='replace')[:2000]}") from exc


def make_plugin_zip() -> bytes:
    manifest = {
        "schema_version": "operly.plugin/v1",
        "plugin_id": "studio.runner.smoke",
        "version": "1.0.0",
        "display_name": "Studio Runner Smoke",
        "description": "Minimal Studio-style sandbox plugin used to prove Railway Runner deployment.",
        "execution_mode": "sandbox_job",
        "capabilities": [
            {
                "id": "studio.runner.smoke.echo",
                "display_name": "Echo",
                "description": "Returns a deterministic payload from an isolated plugin package.",
                "input_schema": {"type": "object", "properties": {"message": {"type": "string"}}},
                "output_schema": {"type": "object", "properties": {"message": {"type": "string"}, "runner": {"type": "string"}}},
                "permissions": [],
                "risk": "read_only",
                "approval_required": False,
                "reversible": False,
                "aliases": [],
                "emits": [],
                "tags": ["studio", "smoke"],
            }
        ],
        "permissions": [],
        "configuration_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "runtime": {
            "profile": "sandbox-job",
            "kind": "job",
            "network": {"mode": "off", "allowed_hosts": []},
            "resources": {
                "cpu_millicores": 500,
                "memory_mb": 768,
                "disk_mb": 2048,
                "max_runtime_seconds": 300,
                "max_concurrency": 1,
            },
        },
        "storage": [],
        "credentials": [],
        "produces_events": [],
        "consumes_events": [],
        "requested_bindings": [],
        "ui": [],
        "metadata": {"source": "studio-smoke"},
    }
    plugin = '''import json\nimport sys\npayload = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}\nprint(json.dumps({"message": payload.get("message", ""), "runner": "studio-smoke-ok"}, sort_keys=True))\n'''
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("operly.plugin.json", json.dumps(manifest, separators=(",", ":"), sort_keys=True))
        zf.writestr("plugin.py", plugin)
    return buf.getvalue()


def main() -> None:
    health = json.loads(urllib.request.urlopen(BASE + "/health", timeout=20).read())
    print("RUNNER_HEALTH", json.dumps(health, sort_keys=True))

    created = signed_request(
        "POST",
        "/v1/computer/sessions",
        {
            "client_session_id": "studio-plugin-live-smoke",
            "workspace_id": "plugin-smoke-isolated",
            "principal_id": "operly-smoke-harness",
            "profile": "coding",
            "ttl_seconds": 600,
            "network_policy": "off",
        },
    )
    runtime_id = str(created.get("session_id") or created.get("id") or "")
    if not runtime_id:
        raise RuntimeError(f"runner did not return a sandbox id: {created}")
    print("SANDBOX_CREATED", runtime_id, created.get("isolation"), "private_network=", created.get("private_network"))

    try:
        package = make_plugin_zip()
        digest = hashlib.sha256(package).hexdigest()
        imported = signed_request(
            "POST",
            f"/v1/computer/sessions/{runtime_id}/tools/artifact.import",
            {"arguments": {"path": "studio-plugin.zip", "content_base64": base64.b64encode(package).decode(), "content_type": "application/zip"}},
        )
        if str(imported.get("sha256") or "").lower() != digest:
            raise RuntimeError(f"artifact digest mismatch: expected {digest}, got {imported.get('sha256')}")
        print("PLUGIN_IMPORTED", digest, "bytes=", len(package))

        code = r'''
import json, pathlib, subprocess, zipfile
archive = pathlib.Path('/workspace/work/studio-plugin.zip')
root = pathlib.Path('/workspace/work/studio-plugin')
root.mkdir(exist_ok=True)
with zipfile.ZipFile(archive) as zf:
    zf.extractall(root)
manifest = json.loads((root / 'operly.plugin.json').read_text())
assert manifest['schema_version'] == 'operly.plugin/v1'
assert manifest['execution_mode'] == 'sandbox_job'
assert manifest['runtime']['profile'] == 'sandbox-job'
proc = subprocess.run(
    ['python3', str(root / 'plugin.py'), json.dumps({'message': 'hello from Studio'})],
    capture_output=True, text=True, check=True,
)
result = json.loads(proc.stdout)
assert result == {'message': 'hello from Studio', 'runner': 'studio-smoke-ok'}
(root / 'smoke-result.json').write_text(json.dumps({'manifest': manifest['plugin_id'], 'result': result}, sort_keys=True))
print(json.dumps({'plugin_id': manifest['plugin_id'], 'execution_mode': manifest['execution_mode'], 'result': result}, sort_keys=True))
'''
        executed = signed_request(
            "POST",
            f"/v1/computer/sessions/{runtime_id}/tools/python.exec",
            {"arguments": {"code": code, "cwd": ".", "timeout_seconds": 120}},
        )
        if int(executed.get("exit_code") or 0) != 0:
            raise RuntimeError(f"plugin execution failed: {executed}")
        stdout = str(executed.get("stdout") or "").strip()
        proof = json.loads(stdout)
        if proof.get("result", {}).get("runner") != "studio-smoke-ok":
            raise RuntimeError(f"unexpected plugin result: {proof}")
        print("PLUGIN_EXECUTED", json.dumps(proof, sort_keys=True))

        exported = signed_request(
            "POST",
            f"/v1/computer/sessions/{runtime_id}/tools/artifact.export",
            {"arguments": {"path": "studio-plugin/smoke-result.json", "max_bytes": 1048576}},
        )
        result_bytes = base64.b64decode(exported.get("content_base64") or "")
        result_doc = json.loads(result_bytes)
        if result_doc.get("result", {}).get("runner") != "studio-smoke-ok":
            raise RuntimeError(f"exported artifact failed verification: {result_doc}")
        print("PLUGIN_ARTIFACT_EXPORTED", exported.get("sha256"), json.dumps(result_doc, sort_keys=True))
        print("STUDIO_PLUGIN_RUNNER_SMOKE=PASS")
    finally:
        try:
            stopped = signed_request("DELETE", f"/v1/computer/sessions/{runtime_id}")
            print("SANDBOX_STOPPED", json.dumps(stopped, sort_keys=True))
        except Exception as exc:
            print("SANDBOX_STOP_FAILED", repr(exc))


if __name__ == "__main__":
    main()
