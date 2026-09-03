from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.request

BROKER_URL = os.getenv("OPERLY_HOSTED_BROKER_URL", "").strip().rstrip("/")
BROKER_TOKEN = os.getenv("OPERLY_HOSTED_BROKER_TOKEN", "").strip()
BRANCH = os.getenv("OPERLY_FULLSTACK_SMOKE_BRANCH", "test/fullstack-worker-deploy").strip()
SERVICE_ID = os.getenv("OPERLY_HOSTED_SMOKE_SERVICE_ID", "").strip()
EXPECTED_URL = os.getenv("OPERLY_HOSTED_SMOKE_URL", "").strip().rstrip("/")


def signed_request(path: str, payload: dict) -> dict:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    canonical = b"POST\n" + path.encode() + b"\n" + raw
    signature = hmac.new(BROKER_TOKEN.encode(), canonical, hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        BROKER_URL + path,
        data=raw,
        method="POST",
        headers={"Authorization": "Bearer " + BROKER_TOKEN, "X-Operly-Signature": signature, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read()
        supplied = response.headers.get("X-Operly-Signature", "")
    expected = hmac.new(BROKER_TOKEN.encode(), body, hashlib.sha256).hexdigest()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise RuntimeError("hosted broker response signature is invalid")
    return json.loads(body.decode())


def get_json(url: str, timeout: float = 15) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode())


def post_json(url: str, value: dict, timeout: float = 15) -> dict:
    raw = json.dumps(value).encode()
    request = urllib.request.Request(url, data=raw, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def wait_public(url: str, timeout: float = 360) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            health = get_json(url + "/health")
            if health.get("ok") is True:
                print("PUBLIC_HEALTH", json.dumps(health, sort_keys=True), flush=True)
                return
        except Exception as exc:
            state = type(exc).__name__ + ":" + str(exc)[:120]
            if state != last:
                print("PUBLIC_WAIT", state, flush=True)
                last = state
        time.sleep(5)
    raise TimeoutError("deployed full-stack service did not become publicly healthy")


def main() -> None:
    if not all([BROKER_URL, BROKER_TOKEN, SERVICE_ID, EXPECTED_URL]):
        raise SystemExit("Worker hosted deployment configuration is missing")
    payload = {"service_id": SERVICE_ID, "branch": BRANCH}
    print("WORKER_HOSTED_DEPLOY_REQUEST", json.dumps(payload, sort_keys=True), flush=True)
    result = signed_request("/v1/hosted/deploy", payload)
    print("WORKER_HOSTED_DEPLOY_ACCEPTED", json.dumps(result, sort_keys=True), flush=True)
    url = str(result["url"]).rstrip("/")
    if url != EXPECTED_URL:
        raise RuntimeError("broker returned an unexpected hosted URL")
    wait_public(url)
    info = get_json(url + "/api/info")
    before = get_json(url + "/api/items")
    created = post_json(url + "/api/items", {"text": "Canonical Worker public E2E"})
    after = get_json(url + "/api/items")
    if info.get("deployed_by") != "operly-worker" or "frontend" not in info.get("stack", []) or "node-api" not in info.get("stack", []):
        raise RuntimeError("public app did not identify as the expected full-stack deployment")
    if not created.get("id") or len(after.get("items", [])) != len(before.get("items", [])) + 1:
        raise RuntimeError("public API mutation did not round-trip")
    print("FULLSTACK_INFO", json.dumps(info, sort_keys=True), flush=True)
    print("FULLSTACK_MUTATION", json.dumps(created, sort_keys=True), flush=True)
    print("OPERLY_WORKER_FULLSTACK_DEPLOY=PASS", flush=True)
    print("PUBLIC_URL=" + url, flush=True)


if __name__ == "__main__":
    main()
