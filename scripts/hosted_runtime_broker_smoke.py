from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.getenv("PORT", "3000"))
BROKER_TOKEN = os.getenv("OPERLY_HOSTED_BROKER_TOKEN", "").strip()
RAILWAY_TOKEN = os.getenv("RAILWAY_TOKEN", "").strip()
ENVIRONMENT_ID = os.getenv("RAILWAY_ENVIRONMENT_ID", "").strip()
ALLOWED_SERVICE_ID = os.getenv("OPERLY_HOSTED_SMOKE_SERVICE_ID", "").strip()
ALLOWED_URL = os.getenv("OPERLY_HOSTED_SMOKE_URL", "").strip().rstrip("/")
GRAPHQL = "https://backboard.railway.com/graphql/v2"


def signature(method: str, path: str, raw: bytes) -> str:
    canonical = method.upper().encode() + b"\n" + path.encode() + b"\n" + raw
    return hmac.new(BROKER_TOKEN.encode(), canonical, hashlib.sha256).hexdigest()


def gql(query: str, variables: dict) -> dict:
    raw = json.dumps({"query": query, "variables": variables}, separators=(",", ":")).encode()
    request = urllib.request.Request(
        GRAPHQL,
        data=raw,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Project-Access-Token": RAILWAY_TOKEN,
            "User-Agent": "Operly-Hosted-Runtime-Broker/1.0 (+https://operly.app)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"Railway API HTTP {error.code}: {detail}") from error
    if payload.get("errors"):
        raise RuntimeError("Railway API: " + json.dumps(payload["errors"])[:2000])
    return payload.get("data") or {}


def deploy(payload: dict) -> dict:
    service_id = str(payload.get("service_id") or "").strip()
    branch = str(payload.get("branch") or "").strip()
    if service_id != ALLOWED_SERVICE_ID or not service_id:
        raise ValueError("hosted deployment target is outside the smoke allowlist")
    if branch != "test/fullstack-worker-deploy":
        raise ValueError("hosted deployment branch is outside the smoke allowlist")
    deployed = gql(
        "mutation serviceInstanceDeployV2($serviceId:String!,$environmentId:String!){serviceInstanceDeployV2(serviceId:$serviceId,environmentId:$environmentId)}",
        {"serviceId": service_id, "environmentId": ENVIRONMENT_ID},
    )
    return {
        "ok": True,
        "provider": "railway",
        "service_id": service_id,
        "deployment_id": deployed["serviceInstanceDeployV2"],
        "url": ALLOWED_URL,
        "authority": "environment_scoped_project_token",
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "OperlyHostedBrokerSmoke/3"

    def log_message(self, fmt: str, *args) -> None:
        print("broker", fmt % args, flush=True)

    def send_payload(self, status: int, value: dict, signed: bool = True) -> None:
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if signed and BROKER_TOKEN:
            self.send_header("X-Operly-Signature", hmac.new(BROKER_TOKEN.encode(), body, hashlib.sha256).hexdigest())
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            return self.send_payload(200, {"ok": True, "service": "operly-hosted-runtime-broker-smoke", "mode": "preprovisioned-slot"}, signed=False)
        return self.send_payload(404, {"detail": "not found"}, signed=False)

    def do_POST(self) -> None:
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 128 * 1024)
            raw = self.rfile.read(length)
            if self.path != "/v1/hosted/deploy":
                return self.send_payload(404, {"detail": "not found"}, signed=False)
            auth = self.headers.get("Authorization", "")
            supplied = self.headers.get("X-Operly-Signature", "")
            expected = signature("POST", self.path, raw)
            if not BROKER_TOKEN or not hmac.compare_digest(auth, "Bearer " + BROKER_TOKEN) or not hmac.compare_digest(supplied, expected):
                return self.send_payload(401, {"detail": "unauthorized"}, signed=False)
            payload = json.loads(raw.decode() or "{}")
            result = deploy(payload)
            print("HOSTED_BROKER_DEPLOYED", json.dumps(result, sort_keys=True), flush=True)
            return self.send_payload(201, result)
        except Exception as exc:
            print("HOSTED_BROKER_ERROR", repr(exc), flush=True)
            return self.send_payload(500, {"detail": "hosted deployment failed"})


if __name__ == "__main__":
    if not all([BROKER_TOKEN, RAILWAY_TOKEN, ENVIRONMENT_ID, ALLOWED_SERVICE_ID, ALLOWED_URL]):
        raise SystemExit("broker configuration is incomplete")
    print(f"Operly hosted runtime broker smoke listening on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
