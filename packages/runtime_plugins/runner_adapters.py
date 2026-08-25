"""External production runner interface and explicitly test-only implementations."""
from __future__ import annotations

import abc
import asyncio
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

from packages.runtime_plugins.interaction_contracts import InteractionContractError, validate_interaction_contract
from packages.runtime_plugins.runner_contracts import BuildSubmission, RunnerResult
from packages.runtime_plugins.runtime_profiles import runtime_capabilities, runtime_profile
from packages.software_projects.source_bundle import SourceBundle
from packages.runtime_plugins.sandbox import SandboxFailure, SandboxUnavailable, validate_runner_url
from packages.model_runtime.trace_context import RuntimeTraceEvent, emit_runtime_trace_event


class RunnerAdapter(abc.ABC):
    implementation = "abstract"
    isolation_profile = "unknown"

    @abc.abstractmethod
    async def submit(self, submission: BuildSubmission, bundle: SourceBundle) -> dict: ...

    async def capabilities(self) -> dict | None:
        return None

    async def status(self, job_id: str) -> dict:
        raise NotImplementedError

    async def cancel(self, job_id: str) -> dict:
        raise NotImplementedError

    async def cleanup(self, job_id: str) -> dict:
        raise NotImplementedError

    async def stop_preview(self, preview_id: str) -> dict:
        raise NotImplementedError


def _safe_runner_payload(method: str, path: str, payload) -> dict:
    """Keep execution evidence while never persisting runner transport grants/source text."""
    if not isinstance(payload, dict):
        return {"method": method, "path": path, "payloadType": type(payload).__name__}
    if path != "/v1/builds":
        return {"method": method, "path": path, "payload": payload}
    submission = dict(payload.get("submission") or {})
    bindings = []
    for raw in submission.get("serviceBindings") or []:
        item = dict(raw or {})
        transport = item.pop("transport", None) or {}
        gateway = str(transport.get("gatewayUrl") or "")
        item["transport"] = {
            "configured": bool(transport),
            "gatewayHost": urlparse(gateway).hostname if gateway else None,
            "runtimeTokenPresent": bool(transport.get("runtimeToken")),
            "migrationTokenPresent": bool(transport.get("migrationToken")),
        }
        bindings.append(item)
    submission["serviceBindings"] = bindings
    raw_bundle = payload.get("bundle") or {}
    files = []
    for item in raw_bundle.get("files") or []:
        content = str((item or {}).get("content") or "")
        files.append(
            {
                "path": (item or {}).get("path"),
                "generatedBy": (item or {}).get("generatedBy"),
                "bytes": len(content.encode("utf-8")),
                "digest": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
        )
    return {
        "method": method,
        "path": path,
        "submission": submission,
        "bundle": {"manifest": raw_bundle.get("manifest"), "files": files},
    }


async def _runner_trace(event_type: str, payload, *, phase: str = "event", classification: str | None = None, retryable: bool | None = None) -> None:
    await emit_runtime_trace_event(
        RuntimeTraceEvent(
            event_type=event_type,
            payload=payload,
            phase=phase,
            resource_id="sandbox_runner_http",
            classification=classification,
            retryable=retryable,
        )
    )


class ExternalRunnerAdapter(RunnerAdapter):
    implementation = "external_https_v1"
    isolation_profile = "remote_container_or_microvm"

    def __init__(self, url=None, token=None):
        self.url = (url or os.getenv("OPERLY_SANDBOX_RUNNER_URL", "")).rstrip("/")
        self.token = token or os.getenv("OPERLY_SANDBOX_RUNNER_TOKEN")
        self._capabilities = None

    async def _request(self, method, path, payload=None):
        if not self.url or not self.token:
            await _runner_trace(
                "runner.http.not_configured",
                {"method": method, "path": path},
                phase="error",
                classification="runner_not_configured",
                retryable=True,
            )
            raise SandboxUnavailable("External isolated runner is not configured")
        self.url = validate_runner_url(self.url)
        raw = json.dumps(payload or {}, sort_keys=True).encode()
        signature = hmac.new(self.token.encode(), raw, hashlib.sha256).hexdigest()
        timeout = aiohttp.ClientTimeout(total=30)
        await _runner_trace("runner.http.request", _safe_runner_payload(method, path, payload or {}))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(method, self.url + path, data=raw, headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json", "X-Operly-Signature": signature}) as response:
                    body = await response.read()
                    supplied_signature = response.headers.get("X-Operly-Signature", "")
                    signature_valid = False
                    if supplied_signature:
                        expected = hmac.new(self.token.encode(), body, hashlib.sha256).hexdigest()
                        signature_valid = hmac.compare_digest(supplied_signature, expected)
                    if response.status in range(200, 300) and not signature_valid:
                        await _runner_trace(
                            "runner.http.invalid_signature",
                            {"method": method, "path": path, "status": response.status},
                            phase="error",
                            classification="runner_response_signature_invalid",
                            retryable=False,
                        )
                        raise SandboxFailure("External runner response signature is invalid")
                    try:
                        parsed = json.loads(body or b"{}")
                    except Exception:
                        parsed = {"rawBody": body.decode("utf-8", errors="replace")[:4000]}
                    await _runner_trace(
                        "runner.http.response",
                        {
                            "method": method,
                            "path": path,
                            "status": response.status,
                            "signed": bool(supplied_signature),
                            "signatureValid": signature_valid if supplied_signature else None,
                            "body": parsed,
                        },
                        phase="error" if response.status not in range(200, 300) else "event",
                        classification="runner_http_rejected" if 400 <= response.status < 500 else "runner_http_failure" if response.status >= 500 else None,
                        retryable=response.status >= 500 or response.status in {408, 409, 425, 429},
                    )
                    if response.status not in range(200, 300):
                        detail = ""
                        if isinstance(parsed, dict):
                            detail = str(parsed.get("error") or parsed.get("detail") or "").strip()
                        message = f"Runner request failed with status {response.status}"
                        if detail:
                            message += f": {detail[:1000]}"
                        error = SandboxFailure(message)
                        error.status = response.status
                        error.response_body = parsed
                        raise error
                    return parsed
        except SandboxFailure:
            raise
        except (aiohttp.ClientError, ValueError) as error:
            await _runner_trace(
                "runner.http.communication_error",
                {"method": method, "path": path, "type": type(error).__name__, "message": str(error)},
                phase="error",
                classification="runner_communication_failure",
                retryable=True,
            )
            raise SandboxFailure("External runner communication failed") from error

    async def capabilities(self) -> dict | None:
        if self._capabilities is not None:
            return self._capabilities
        try:
            self._capabilities = await self._request("GET", "/v1/capabilities")
        except SandboxFailure as error:
            if "status 404" not in str(error):
                raise
            self._capabilities = {
                "protocolVersion": 0,
                "legacy": True,
                "profiles": {"python-stdlib-web": {"profileVersion": 1}},
            }
        return self._capabilities

    async def submit(self, submission, bundle):
        return await self._request("POST", "/v1/builds", {"submission": submission.model_dump(mode="json"), "bundle": {"manifest": bundle.manifest, "files": [{"path": x.path, "content": x.content.decode(), "generatedBy": x.generated_by} for x in bundle.files]}})

    async def status(self, job_id):
        return await self._request("GET", f"/v1/builds/{job_id}")

    async def cancel(self, job_id):
        return await self._request("POST", f"/v1/builds/{job_id}/cancel")

    async def cleanup(self, job_id):
        return await self._request("POST", f"/v1/builds/{job_id}/cleanup")

    async def stop_preview(self, preview_id):
        return await self._request("DELETE", f"/v1/previews/{preview_id}")


class FakeRunnerAdapter(RunnerAdapter):
    implementation = "fake_test_only"
    isolation_profile = "none_fake"

    def __init__(self, fail_at=None):
        self.fail_at = fail_at
        self.jobs = {}

    async def capabilities(self):
        return runtime_capabilities()

    async def submit(self, submission, bundle):
        job_id = f"fake-{len(self.jobs) + 1}"
        ok = self.fail_at is None
        result = RunnerResult(buildSuccess=ok, testSuccess=ok, processStartSuccess=ok, healthCheckSuccess=ok, acceptanceCheckSuccess=ok, previewAvailable=ok, failureEvidence={} if ok else {"classification": self.fail_at, "message": "deterministic fake failure"})
        data = {"jobId": job_id, "state": "preview_ready" if ok else "failed", "result": result.model_dump(), "events": [{"state": "created"}, {"state": self.fail_at or "preview_ready"}], "preview": {"id": f"preview-{job_id}", "targetUrl": "http://runner.invalid"} if ok else None}
        self.jobs[job_id] = data
        return data

    async def cancel(self, job_id):
        self.jobs[job_id]["state"] = "cancelled"; return {"state": "cancelled"}

    async def status(self, job_id):
        return self.jobs[job_id]

    async def cleanup(self, job_id):
        self.jobs[job_id]["state"] = "cleaned"; return {"state": "cleaned"}

    async def stop_preview(self, preview_id):
        return {"state": "stopped"}


class LocalSubprocessTestRunner(RunnerAdapter):
    """Integration-test runner. Process isolation only; never selectable in production."""
    implementation = "local_subprocess_test_only"
    isolation_profile = "constrained_subprocess_not_os_isolated"

    def __init__(self):
        if os.getenv("OPERLY_ENABLE_TEST_SUBPROCESS_RUNNER") != "1" or os.getenv("OPERLY_ENV", "test") not in {"test", "development"}:
            raise SandboxUnavailable("Test subprocess runner is disabled")
        self.jobs = {}
        self.previews = {}

    async def capabilities(self):
        caps = runtime_capabilities()
        profiles = dict(caps["profiles"])
        if not shutil.which("node"):
            profiles.pop("static-web-js", None)
        return {**caps, "profiles": profiles}

    async def submit(self, submission, bundle):
        profile = runtime_profile(submission.stackId)
        root = Path(tempfile.mkdtemp(prefix="operly-runner-test-"))
        source = root / "source"
        for path in (source, root / "runtime", root / "artifacts", root / "logs", root / "tmp"):
            path.mkdir()
        for item in bundle.files:
            target = source / item.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(item.content)

        env = {"PYTHONPATH": str(source), "PATH": os.environ.get("PATH", "")}
        for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP"):
            if os.environ.get(key):
                env[key] = os.environ[key]
        events = []

        def local_command(command, port=None):
            args = list(command)
            if args and args[0] == "python":
                args[0] = sys.executable
            if args and args[0] == "node" and not shutil.which("node"):
                raise SandboxUnavailable("Node is not available in the test runner")
            if port is not None:
                args = [str(port) if item == "8080" else "127.0.0.1" if item == "0.0.0.0" else item for item in args]
            return args

        def run(label, args, timeout=30):
            try:
                result = subprocess.run(args, cwd=source, env=env, capture_output=True, text=True, timeout=min(timeout, submission.maxDurationSeconds))
            except subprocess.TimeoutExpired:
                events.append({"state": "timed_out", "exitCode": None, "message": "Typed operation exceeded its execution limit"}); return None
            output = result.stdout + result.stderr
            if len(output.encode()) > submission.resources.logBytes:
                events.append({"state": "resource_exceeded", "exitCode": result.returncode, "message": "Log output exceeded policy"}); return None
            events.append({"state": label, "exitCode": result.returncode, "message": output[-4000:]})
            return result

        phase_map = (("static_analysis", "static_analysis", "build_failure"), ("building", "build", "build_failure"), ("testing", "test", "test_failure"))
        for event_state, command_key, failure in phase_map:
            result = await asyncio.to_thread(run, event_state, local_command(profile["commands"][command_key]))
            if result is None:
                return _failed(root.name, events, "resource_violation")
            if result.returncode:
                return _failed(root.name, events, failure)

        port = await asyncio.to_thread(_free_port)
        process = subprocess.Popen(local_command(profile["commands"]["start"], port), cwd=source, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        url = f"http://127.0.0.1:{port}"
        healthy, health_body = False, ""
        for _ in range(40):
            try:
                with urllib.request.urlopen(url + submission.healthCheck.path, timeout=1) as response:
                    health_body = response.read().decode(errors="replace")
                    healthy = response.status == submission.healthCheck.expectedStatus and (not submission.healthCheck.bodyMarker or submission.healthCheck.bodyMarker in health_body)
            except Exception:
                await asyncio.sleep(.1)
            if healthy:
                break
        if not healthy:
            process.terminate()
            return {"jobId": root.name, "state": "failed", "result": RunnerResult(buildSuccess=True, testSuccess=True, processStartSuccess=process.poll() is None, failureEvidence={"classification": "health_check_failure", "message": "Configured health check did not pass"}).model_dump(), "events": events}

        try:
            interaction_report = validate_interaction_contract(bundle)
        except InteractionContractError as error:
            process.terminate()
            return _failed(
                root.name,
                events + [{"state": "acceptance_failed", "message": str(error)}],
                "acceptance_failure",
            )
        acceptance = {
            "passed": True,
            "basis": "executable tests, interaction-contract coverage, and declared health contract",
            "interactionContract": interaction_report,
        }
        preview_id = "preview-" + root.name
        self.jobs[root.name] = {"root": root, "process": process}
        self.previews[preview_id] = {"job": root.name, "url": url}
        result = RunnerResult(buildSuccess=True, testSuccess=True, processStartSuccess=True, healthCheckSuccess=True, acceptanceCheckSuccess=True, previewAvailable=True, artifacts=[], testReport={"unit": "passed", "acceptance": acceptance}, staticAnalysisReport={"profile": submission.stackId, "passed": True}, dependencyReport={"dependencies": [], "networkUsed": False}, resourceUsage={"profile": "test_subprocess", "limitsEnforced": "timeout and bounded output only"})
        return {"jobId": root.name, "state": "preview_ready", "result": result.model_dump(), "events": events + [{"state": "preview_ready", "message": json.dumps(acceptance)}], "preview": {"id": preview_id, "targetUrl": url}}

    async def cancel(self, job_id):
        return await self.cleanup(job_id)

    async def status(self, job_id):
        return {"jobId": job_id, "state": "preview_ready" if job_id in self.jobs else "cleaned"}

    async def cleanup(self, job_id):
        job = self.jobs.pop(job_id, None)
        if job and job["process"].poll() is None:
            job["process"].terminate(); job["process"].wait(timeout=5)
        if job:
            shutil.rmtree(job["root"], ignore_errors=True)
        return {"state": "cleaned"}

    async def stop_preview(self, preview_id):
        item = self.previews.pop(preview_id, None)
        return await self.cleanup(item["job"]) if item else {"state": "cleaned"}


def _failed(job_id, events, classification):
    return {"jobId": job_id, "state": "failed", "result": RunnerResult(failureEvidence={"classification": classification, "log": events[-1]["message"] if events else ""}).model_dump(), "events": events}


def _free_port():
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); return sock.getsockname()[1]
