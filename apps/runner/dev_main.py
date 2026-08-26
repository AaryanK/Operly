"""Development-only runner sidecar using the production runner HTTP contract.

The Operly control plane still talks exclusively through ``ExternalRunnerAdapter``.
This process exists only so local development can exercise the same signed
``/v1/builds`` protocol and preview lifecycle as production while using the existing
process-isolated test runners underneath.

It is intentionally impossible to enable in production.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from urllib.parse import urlparse

import aiohttp
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from packages.custom_software.fullstack_subprocess_runner import FullStackSubprocessTestRunner
from packages.custom_software.runner_adapters import LocalSubprocessTestRunner, RunnerAdapter
from packages.custom_software.runner_contracts import BuildSubmission
from packages.custom_software.sandbox import SandboxUnavailable
from packages.custom_software.source_bundles import SourceFile, build_bundle
from packages.runtime_plugins import FULLSTACK_RUNTIME_ID


@dataclass
class _Job:
    adapter: RunnerAdapter
    response: dict
    preview_id: str | None = None
    preview_token: str | None = None
    internal_preview_url: str | None = None


app = FastAPI(title="Operly Development Runner Sidecar", version="1-dev")
_jobs: dict[str, _Job] = {}
_previews: dict[str, str] = {}
_idempotency: dict[str, str] = {}
_standard_runner: LocalSubprocessTestRunner | None = None
_fullstack_runner: FullStackSubprocessTestRunner | None = None


def _config() -> tuple[str, str]:
    env = os.getenv("OPERLY_ENV", "").strip().lower()
    if env not in {"development", "test"}:
        raise SandboxUnavailable("Development runner sidecar is disabled outside development/test")
    if os.getenv("OPERLY_ENABLE_LOCAL_RUNNER_SIDECAR", "") != "1":
        raise SandboxUnavailable("Development runner sidecar requires OPERLY_ENABLE_LOCAL_RUNNER_SIDECAR=1")
    if os.getenv("OPERLY_ENABLE_TEST_SUBPROCESS_RUNNER", "") != "1":
        raise SandboxUnavailable("Development runner sidecar requires OPERLY_ENABLE_TEST_SUBPROCESS_RUNNER=1")

    token = os.getenv("OPERLY_SANDBOX_RUNNER_TOKEN", "").strip()
    if len(token) < 32:
        raise SandboxUnavailable("Development runner token must be at least 32 characters")

    public_base_url = os.getenv(
        "OPERLY_LOCAL_RUNNER_PUBLIC_BASE_URL",
        "http://127.0.0.1:8091",
    ).rstrip("/")
    parsed = urlparse(public_base_url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "http"
        or host not in {"127.0.0.1", "localhost"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise SandboxUnavailable("Local runner public base URL must be an HTTP loopback origin")
    return token, public_base_url


def _runners() -> tuple[LocalSubprocessTestRunner, FullStackSubprocessTestRunner]:
    global _standard_runner, _fullstack_runner
    _config()
    if _standard_runner is None:
        _standard_runner = LocalSubprocessTestRunner()
    if _fullstack_runner is None:
        _fullstack_runner = FullStackSubprocessTestRunner()
    return _standard_runner, _fullstack_runner


def _signed_json(payload: dict, status_code: int = 200) -> JSONResponse:
    token, _ = _config()
    response = JSONResponse(payload, status_code=status_code)
    response.headers["X-Operly-Signature"] = hmac.new(
        token.encode(), response.body, hashlib.sha256
    ).hexdigest()
    response.headers["Cache-Control"] = "no-store"
    return response


async def _authenticate(request: Request) -> bytes:
    token, _ = _config()
    authorization = request.headers.get("Authorization", "")
    if not hmac.compare_digest(authorization, f"Bearer {token}"):
        raise HTTPException(status_code=401, detail="Invalid runner authorization")
    raw = await request.body()
    expected = hmac.new(token.encode(), raw, hashlib.sha256).hexdigest()
    supplied = request.headers.get("X-Operly-Signature", "")
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid runner request signature")
    return raw


def _bundle_from_payload(submission: BuildSubmission, raw_bundle: dict):
    if not isinstance(raw_bundle, dict):
        raise ValueError("bundle must be an object")
    manifest = raw_bundle.get("manifest")
    rows = raw_bundle.get("files")
    if not isinstance(manifest, dict) or not isinstance(rows, list):
        raise ValueError("bundle manifest/files are required")

    files: list[SourceFile] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("bundle file entries must be objects")
        path = row.get("path")
        content = row.get("content")
        generated_by = row.get("generatedBy") or "coding_harness"
        if not isinstance(path, str) or not isinstance(content, str):
            raise ValueError("bundle file path/content must be strings")
        files.append(SourceFile(path, content.encode("utf-8"), str(generated_by)))

    required = {
        "workspaceId",
        "applicationId",
        "planId",
        "planVersion",
        "sourceVersion",
        "promptDigest",
    }
    if not required <= set(manifest):
        raise ValueError("bundle manifest is incomplete")
    if str(manifest["workspaceId"]) != submission.workspaceId:
        raise ValueError("bundle workspace does not match submission")
    if str(manifest["applicationId"]) != submission.applicationId:
        raise ValueError("bundle application does not match submission")
    if int(manifest["planVersion"]) != submission.planVersion:
        raise ValueError("bundle plan version does not match submission")
    if int(manifest["sourceVersion"]) != submission.sourceVersion:
        raise ValueError("bundle source version does not match submission")

    rebuilt = build_bundle(
        files,
        submission.workspaceId,
        submission.applicationId,
        str(manifest["planId"]),
        submission.planVersion,
        submission.sourceVersion,
        str(manifest["promptDigest"]),
    )
    if rebuilt.digest != submission.sourceBundleDigest:
        raise ValueError("source bundle digest mismatch")
    if rebuilt.manifest != manifest:
        raise ValueError("source bundle manifest mismatch")
    return rebuilt


def _select_adapter(submission: BuildSubmission) -> RunnerAdapter:
    standard, fullstack = _runners()
    return fullstack if submission.stackId == FULLSTACK_RUNTIME_ID else standard


def _publish_preview(response: dict) -> tuple[dict, str | None, str | None, str | None]:
    response = dict(response)
    preview = response.get("preview")
    if not isinstance(preview, dict):
        return response, None, None, None
    internal = str(preview.get("targetUrl") or "").strip()
    if not internal:
        return response, None, None, None
    _, public_base_url = _config()
    preview_id = str(preview.get("id") or "preview-" + secrets.token_hex(8))
    token = secrets.token_urlsafe(24)
    _previews[token] = internal.rstrip("/")
    response["preview"] = {
        **preview,
        "id": preview_id,
        "targetUrl": f"{public_base_url}/preview/{token}/",
    }
    return response, preview_id, token, internal


def _revoke_preview(item: _Job) -> None:
    if item.preview_token:
        _previews.pop(item.preview_token, None)
    item.preview_token = None
    item.internal_preview_url = None


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, error: HTTPException):
    payload = {"detail": error.detail}
    if request.url.path.startswith("/v1/"):
        try:
            return _signed_json(payload, error.status_code)
        except SandboxUnavailable:
            pass
    return JSONResponse(payload, status_code=error.status_code, headers={"Cache-Control": "no-store"})


@app.get("/health")
async def health():
    try:
        _runners()
    except SandboxUnavailable as error:
        return JSONResponse(
            {"status": "not_ready", "reason": str(error)},
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(
        {"status": "ready", "isolation": "development_subprocess_sidecar"},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/v1/capabilities")
async def capabilities(request: Request):
    await _authenticate(request)
    standard, fullstack = _runners()
    standard_caps = dict(await standard.capabilities() or {})
    fullstack_caps = dict(await fullstack.capabilities() or {})
    profiles = dict(standard_caps.get("profiles") or {})
    profiles.update(fullstack_caps.get("profiles") or {})
    return _signed_json(
        {
            "protocolVersion": max(
                int(standard_caps.get("protocolVersion") or 0),
                int(fullstack_caps.get("protocolVersion") or 0),
            ),
            "profiles": profiles,
        }
    )


@app.post("/v1/builds")
async def create_build(request: Request):
    raw = await _authenticate(request)
    try:
        payload = json.loads(raw or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("request body must be an object")
        submission = BuildSubmission.model_validate(payload.get("submission"))
        bundle = _bundle_from_payload(submission, payload.get("bundle"))
    except (json.JSONDecodeError, ValidationError, ValueError) as error:
        return _signed_json({"error": str(error)}, 400)

    existing_id = _idempotency.get(submission.idempotencyKey)
    if existing_id and existing_id in _jobs:
        return _signed_json(_jobs[existing_id].response)

    adapter = _select_adapter(submission)
    try:
        response = dict(await adapter.submit(submission, bundle) or {})
    except Exception as error:
        return _signed_json(
            {
                "error": "Development runner execution failed",
                "classification": type(error).__name__,
            },
            503,
        )

    job_id = str(response.get("jobId") or "").strip()
    if not job_id:
        return _signed_json({"error": "Runner backend did not return a jobId"}, 503)
    response, preview_id, preview_token, internal_preview = _publish_preview(response)
    _jobs[job_id] = _Job(
        adapter=adapter,
        response=response,
        preview_id=preview_id,
        preview_token=preview_token,
        internal_preview_url=internal_preview,
    )
    _idempotency[submission.idempotencyKey] = job_id
    return _signed_json(response, 202 if response.get("state") in {"queued", "running"} else 200)


@app.get("/v1/builds/{job_id}")
async def build_status(job_id: str, request: Request):
    await _authenticate(request)
    item = _jobs.get(job_id)
    if item is None:
        return _signed_json({"error": "Build not found"}, 404)
    return _signed_json(item.response)


@app.post("/v1/builds/{job_id}/cancel")
async def cancel_build(job_id: str, request: Request):
    await _authenticate(request)
    item = _jobs.get(job_id)
    if item is None:
        return _signed_json({"error": "Build not found"}, 404)
    result = dict(await item.adapter.cancel(job_id) or {})
    _revoke_preview(item)
    item.preview_id = None
    item.response = {"jobId": job_id, **result}
    return _signed_json(item.response)


@app.post("/v1/builds/{job_id}/cleanup")
async def cleanup_build(job_id: str, request: Request):
    await _authenticate(request)
    item = _jobs.get(job_id)
    if item is None:
        return _signed_json({"error": "Build not found"}, 404)
    result = dict(await item.adapter.cleanup(job_id) or {})
    _revoke_preview(item)
    item.preview_id = None
    item.response = {"jobId": job_id, **result}
    return _signed_json(item.response)


@app.delete("/v1/previews/{preview_id}")
async def stop_preview(preview_id: str, request: Request):
    await _authenticate(request)
    item = next((row for row in _jobs.values() if row.preview_id == preview_id), None)
    if item is None:
        return _signed_json({"state": "cleaned"})
    result = dict(await item.adapter.stop_preview(preview_id) or {})
    _revoke_preview(item)
    item.preview_id = None
    item.response = {**item.response, "preview": None}
    return _signed_json(result)


_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "host",
}


@app.api_route(
    "/preview/{token}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def preview(token: str, path: str, request: Request):
    upstream = _previews.get(token)
    if upstream is None:
        raise HTTPException(status_code=404, detail="Preview not found")
    target = upstream + "/" + path
    if request.url.query:
        target += "?" + request.url.query
    body = await request.body()
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _HOP_HEADERS
    }
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(
            request.method,
            target,
            data=body or None,
            headers=headers,
            allow_redirects=False,
        ) as response:
            content = await response.read()
            forwarded = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in _HOP_HEADERS
            }
            forwarded["Cache-Control"] = "no-store"
            return Response(
                content=content,
                status_code=response.status,
                headers=forwarded,
                media_type=response.headers.get("Content-Type"),
            )


__all__ = ["app"]
