"""Authenticated external runner API consumed by ``ExternalRunnerAdapter``.

The gateway is deliberately small. It validates and persists a request, returns a
queued job immediately, then executes the job on a dedicated Docker isolation host.
Generated software never runs in this FastAPI process.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from apps.runner.docker_backend import DockerIsolationBackend, IsolationUnavailable
from apps.runner.store import RunnerStore
from packages.custom_software.runner_contracts import BuildSubmission
from packages.custom_software.source_bundles import SourceFile, build_bundle
from packages.runtime_plugins import (
    FULLSTACK_PROFILE_VERSION,
    FULLSTACK_RUNTIME_ID,
    validate_fullstack_source,
)


class RunnerConfig:
    def __init__(self):
        self.token = os.getenv("OPERLY_RUNNER_TOKEN", "")
        self.public_base_url = os.getenv("OPERLY_RUNNER_PUBLIC_BASE_URL", "").rstrip("/")
        self.errors: list[str] = []
        if len(self.token) < 32:
            self.errors.append("runner_token_invalid")
        parsed = urlparse(self.public_base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            self.errors.append("public_base_url_invalid")


config = RunnerConfig()
store = RunnerStore()
backend: DockerIsolationBackend | None = None
backend_error: str | None = None
_tasks: set[asyncio.Task] = set()


def _signed_json(payload: dict, status_code: int = 200) -> JSONResponse:
    response = JSONResponse(payload, status_code=status_code)
    body = response.body
    if config.token:
        response.headers["X-Operly-Signature"] = hmac.new(
            config.token.encode(), body, hashlib.sha256
        ).hexdigest()
    response.headers["Cache-Control"] = "no-store"
    return response


def _failure_response(job_id: str, classification: str, message: str) -> dict:
    return {
        "jobId": job_id,
        "state": "failed",
        "result": {
            "buildSuccess": False,
            "testSuccess": False,
            "processStartSuccess": False,
            "healthCheckSuccess": False,
            "acceptanceCheckSuccess": False,
            "previewAvailable": False,
            "artifacts": [],
            "testReport": {},
            "staticAnalysisReport": {},
            "dependencyReport": {},
            "resourceUsage": {},
            "failureEvidence": {
                "classification": classification,
                "message": message[:1000],
            },
        },
    }


async def _authenticate(request: Request) -> bytes:
    if config.errors or backend is None:
        raise HTTPException(status_code=503, detail="Runner is not ready")
    authorization = request.headers.get("Authorization", "")
    if not hmac.compare_digest(authorization, f"Bearer {config.token}"):
        raise HTTPException(status_code=401, detail="Invalid runner authorization")
    raw = await request.body()
    expected = hmac.new(config.token.encode(), raw, hashlib.sha256).hexdigest()
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

    required_manifest = {
        "workspaceId",
        "applicationId",
        "planId",
        "planVersion",
        "sourceVersion",
        "promptDigest",
    }
    if not required_manifest <= set(manifest):
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
    validation = validate_fullstack_source(rebuilt)
    if not validation.valid:
        raise ValueError("; ".join(validation.errors))
    return rebuilt


def _validated_request(payload: dict):
    if not isinstance(payload, dict):
        raise ValueError("request body must be an object")
    submission = BuildSubmission.model_validate(payload.get("submission"))
    if submission.stackId != FULLSTACK_RUNTIME_ID:
        raise ValueError("production runner only accepts operly-fullstack-v1")
    if submission.stackVersion != FULLSTACK_PROFILE_VERSION:
        raise ValueError("production runner profile version mismatch")
    bundle = _bundle_from_payload(submission, payload.get("bundle"))
    return submission, bundle


def _current_response(job) -> dict:
    if job.response:
        return job.response
    return {"jobId": job.id, "state": job.state}


def _append_event(job_id: str, event: dict) -> None:
    job = store.get(job_id)
    current = _current_response(job)
    events = list(current.get("events") or [])
    events.append(event)
    state = str(event.get("state") or job.state)
    store.update(
        job_id,
        state=state,
        response={"jobId": job_id, "state": state, "events": events},
    )


def _execute_job(job_id: str) -> None:
    if backend is None:
        response = _failure_response(
            job_id,
            "runner_unavailable",
            "Isolation backend became unavailable before execution",
        )
        store.update(job_id, state="failed", response=response)
        store.remove_request(job_id)
        return
    job = store.get(job_id)
    try:
        payload = json.loads(Path(job.request_path).read_text())
        submission, bundle = _validated_request(payload)
        outcome = backend.run_job(
            submission,
            bundle,
            job_id=job_id,
            event_callback=lambda event: _append_event(job_id, event),
            cancelled=lambda: store.get(job_id).cancel_requested,
        )
        response = dict(outcome.response)
        classification = (
            response.get("result", {})
            .get("failureEvidence", {})
            .get("classification")
        )
        if classification == "cancelled":
            response["state"] = "cancelled"
        if response.get("state") == "preview_ready":
            preview = dict(response.get("preview") or {})
            preview_id = str(preview.get("id") or f"preview-{job_id}")
            preview_token = secrets.token_urlsafe(32)
            preview["id"] = preview_id
            preview["targetUrl"] = f"{config.public_base_url}/preview/{preview_token}/"
            response["preview"] = preview
            store.update(
                job_id,
                state="preview_ready",
                response=response,
                resources=outcome.resources,
                preview_id=preview_id,
                preview_token=preview_token,
                preview_upstream=outcome.preview_upstream,
            )
        else:
            store.update(job_id, state=response.get("state", "failed"), response=response)
            store.remove_request(job_id)
    except Exception as error:
        response = _failure_response(
            job_id,
            "runner_infrastructure_failure",
            str(error),
        )
        store.update(job_id, state="failed", response=response)
        store.remove_request(job_id)


async def _schedule(job_id: str) -> None:
    await asyncio.to_thread(_execute_job, job_id)


def _launch(job_id: str) -> None:
    task = asyncio.create_task(_schedule(job_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global backend, backend_error
    backend = None
    backend_error = None
    if not config.errors:
        try:
            backend = await asyncio.to_thread(DockerIsolationBackend)
        except Exception as error:
            backend_error = type(error).__name__

    # A queued/building job can never be declared successful after a gateway
    # restart. Mark it durably failed even if Docker itself is temporarily down.
    for job in store.in_flight():
        if backend is not None:
            try:
                await asyncio.to_thread(backend.cleanup_job_id, job.id)
            except Exception:
                pass
        response = _failure_response(
            job.id,
            "runner_restart",
            "Runner restarted while the job was in flight",
        )
        store.update(job.id, state="failed", response=response)
        store.remove_request(job.id)

    # Preview-ready is also evidence-based. Preserve it across a normal gateway
    # restart only when the read-only runtime container still exists and is running.
    if backend is not None:
        for job in store.preview_ready():
            inspection = await asyncio.to_thread(backend.inspect_runtime, job.resources)
            if inspection.get("status") == "running" and inspection.get("readOnlyRootfs"):
                continue
            try:
                await asyncio.to_thread(backend.cleanup_job_id, job.id)
            except Exception:
                pass
            response = _failure_response(
                job.id,
                "preview_lost_after_restart",
                "Persisted preview runtime was not alive after runner restart",
            )
            store.update(job.id, state="failed", response=response)
            store.remove_request(job.id)
    yield


app = FastAPI(title="Operly Isolated Runner", version="1", lifespan=lifespan)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, error: HTTPException):
    payload = {"detail": error.detail}
    if request.url.path.startswith("/v1/"):
        return _signed_json(payload, error.status_code)
    return JSONResponse(payload, status_code=error.status_code, headers={"Cache-Control": "no-store"})


@app.get("/health")
async def health():
    ready = not config.errors and backend is not None
    if ready:
        payload = {
            "status": "ready",
            "isolation": backend.isolation_profile,
        }
    else:
        payload = {
            "status": "not_ready",
            "reason": "configuration_invalid" if config.errors else "isolation_backend_unavailable",
        }
    return JSONResponse(payload, status_code=200 if ready else 503, headers={"Cache-Control": "no-store"})


@app.get("/v1/capabilities")
async def capabilities(request: Request):
    await _authenticate(request)
    assert backend is not None
    try:
        payload = await asyncio.to_thread(backend.capabilities)
    except IsolationUnavailable:
        return _signed_json({"error": "isolation_backend_unavailable"}, 503)
    return _signed_json(payload)


@app.post("/v1/builds")
async def create_build(request: Request):
    raw = await _authenticate(request)
    try:
        payload = json.loads(raw or b"{}")
        submission, _bundle = _validated_request(payload)
    except (json.JSONDecodeError, ValidationError, ValueError) as error:
        return _signed_json({"error": str(error)}, 400)

    existing = store.by_idempotency(submission.idempotencyKey)
    if existing is not None:
        return _signed_json(_current_response(existing), 200)

    job_id = uuid.uuid4().hex
    record = store.create(job_id, submission.idempotencyKey, payload)
    if record.id != job_id:
        return _signed_json(_current_response(record), 200)
    _launch(job_id)
    return _signed_json({"jobId": job_id, "state": "queued"}, 202)


@app.get("/v1/builds/{job_id}")
async def build_status(job_id: str, request: Request):
    await _authenticate(request)
    try:
        job = store.get(job_id)
    except KeyError:
        return _signed_json({"error": "Build not found"}, 404)
    return _signed_json(_current_response(job))


@app.post("/v1/builds/{job_id}/cancel")
async def cancel_build(job_id: str, request: Request):
    await _authenticate(request)
    try:
        job = store.get(job_id)
    except KeyError:
        return _signed_json({"error": "Build not found"}, 404)
    if job.state in {"failed", "cancelled", "cleaned"}:
        return _signed_json({"state": job.state})
    store.request_cancel(job_id)
    if job.state == "preview_ready" and backend is not None:
        await asyncio.to_thread(backend.cleanup, job.resources)
        await asyncio.to_thread(backend.cleanup_job_id, job_id)
        store.update(job_id, state="cancelled", response={"jobId": job_id, "state": "cancelled"})
        store.remove_request(job_id)
    return _signed_json({"state": "cancel_requested"})


@app.post("/v1/builds/{job_id}/cleanup")
async def cleanup_build(job_id: str, request: Request):
    await _authenticate(request)
    try:
        job = store.get(job_id)
    except KeyError:
        return _signed_json({"error": "Build not found"}, 404)
    if backend is not None:
        await asyncio.to_thread(backend.cleanup, job.resources)
        await asyncio.to_thread(backend.cleanup_job_id, job_id)
    store.update(job_id, state="cleaned", response={"jobId": job_id, "state": "cleaned"})
    store.remove_request(job_id)
    return _signed_json({"state": "cleaned"})


@app.delete("/v1/previews/{preview_id}")
async def stop_preview(preview_id: str, request: Request):
    await _authenticate(request)
    job = store.by_preview_id(preview_id)
    if job is None:
        return _signed_json({"state": "cleaned"})
    if backend is not None:
        await asyncio.to_thread(backend.cleanup, job.resources)
        await asyncio.to_thread(backend.cleanup_job_id, job.id)
    store.update(job.id, state="cleaned", response={"jobId": job.id, "state": "cleaned"})
    store.remove_request(job.id)
    return _signed_json({"state": "cleaned"})


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
    job = store.by_preview_token(token)
    if job is None or job.state != "preview_ready" or not job.preview_upstream:
        raise HTTPException(status_code=404, detail="Preview not found")
    target = job.preview_upstream.rstrip("/") + "/" + path
    if request.url.query:
        target += "?" + request.url.query
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _HOP_HEADERS and key.lower() != "authorization"
    }
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            upstream = await client.request(
                request.method,
                target,
                headers=headers,
                content=body,
            )
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail="Preview runtime unavailable") from error
    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in _HOP_HEADERS
    }
    location = response_headers.get("location")
    if location and location.startswith("/"):
        response_headers["location"] = f"/preview/{token}{location}"
    response_headers["Cache-Control"] = "no-store"
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
    )
