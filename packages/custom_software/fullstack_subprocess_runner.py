"""Reference executor for ``operly-fullstack-v1`` in tests/development only.

This runner proves the deterministic full-stack lifecycle (install, analyze, build,
test, start, health, acceptance, cleanup) without weakening production isolation.
It is intentionally process-isolated only and cannot be selected in production.
Production must use ``ExternalRunnerAdapter`` backed by a container/microVM runner
that advertises the same profile/version.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from packages.custom_software.runner_adapters import RunnerAdapter
from packages.custom_software.runner_contracts import BuildSubmission, RunnerResult
from packages.custom_software.runtime_profiles import runtime_capabilities
from packages.custom_software.sandbox import SandboxUnavailable
from packages.runtime_plugins import (
    FULLSTACK_PROFILE_VERSION,
    FULLSTACK_RUNTIME_ID,
    parse_fullstack_manifest,
    validate_fullstack_source,
)


class FullStackSubprocessTestRunner(RunnerAdapter):
    implementation = "fullstack_subprocess_test_only"
    isolation_profile = "constrained_subprocess_not_os_isolated"

    def __init__(self):
        if (
            os.getenv("OPERLY_ENABLE_TEST_SUBPROCESS_RUNNER") != "1"
            or os.getenv("OPERLY_ENV", "test") not in {"test", "development"}
        ):
            raise SandboxUnavailable("Full-stack subprocess test runner is disabled")
        self.jobs: dict[str, dict] = {}
        self.previews: dict[str, dict] = {}

    async def capabilities(self):
        capabilities = runtime_capabilities()
        profile = capabilities["profiles"].get(FULLSTACK_RUNTIME_ID)
        return {
            "protocolVersion": capabilities["protocolVersion"],
            "profiles": {FULLSTACK_RUNTIME_ID: profile} if profile else {},
        }

    async def submit(self, submission: BuildSubmission, bundle):
        if submission.stackId != FULLSTACK_RUNTIME_ID:
            raise ValueError("FullStackSubprocessTestRunner only accepts operly-fullstack-v1")
        if submission.stackVersion != FULLSTACK_PROFILE_VERSION:
            raise ValueError("Full-stack runtime profile version mismatch")
        validation = validate_fullstack_source(bundle)
        if not validation.valid:
            return self._failed("invalid-source", [], "security_policy_violation", "; ".join(validation.errors))

        manifest = parse_fullstack_manifest(bundle)
        root = Path(tempfile.mkdtemp(prefix="operly-fullstack-test-"))
        source = root / "source"
        runtime = root / "runtime"
        for path in (source, runtime, root / "artifacts", root / "logs", root / "tmp"):
            path.mkdir(parents=True, exist_ok=True)
        for item in bundle.files:
            target = source / item.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(item.content)

        bindings_file = runtime / "operly-bindings.json"
        bindings_file.write_text(
            json.dumps(
                [binding.model_dump(mode="json") for binding in submission.serviceBindings],
                sort_keys=True,
            )
        )
        env = {
            "PYTHONPATH": str(source),
            "PATH": os.environ.get("PATH", ""),
            "OPERLY_BINDINGS_FILE": str(bindings_file),
        }
        for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME"):
            if os.environ.get(key):
                env[key] = os.environ[key]

        events: list[dict] = []
        python_executable = sys.executable

        def run(label: str, args: list[str], *, cwd: Path = source, timeout: int = 60):
            try:
                result = subprocess.run(
                    args,
                    cwd=cwd,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=min(timeout, submission.maxDurationSeconds),
                )
            except subprocess.TimeoutExpired:
                events.append({"state": "timed_out", "exitCode": None, "message": f"{label} exceeded its execution limit"})
                return None
            output = (result.stdout or "") + (result.stderr or "")
            if len(output.encode()) > submission.resources.logBytes:
                events.append({"state": "resource_exceeded", "exitCode": result.returncode, "message": "Log output exceeded policy"})
                return None
            events.append({"state": label, "exitCode": result.returncode, "message": output[-4000:]})
            return result

        if submission.dependencies:
            if submission.installNetwork.mode != "dependency_registry_only":
                return self._failed(root.name, events, "security_policy_violation", "Dependency install network is not registry bounded")
            if os.getenv("OPERLY_TEST_RUNNER_ALLOW_DEPENDENCY_INSTALL") != "1":
                return self._failed(
                    root.name,
                    events,
                    "dependency_failure",
                    "Test runner dependency installation requires explicit OPERLY_TEST_RUNNER_ALLOW_DEPENDENCY_INSTALL=1",
                )
            if any(item.ecosystem == "python" for item in submission.dependencies):
                venv = runtime / "venv"
                created = await asyncio.to_thread(run, "dependency_resolution", [sys.executable, "-m", "venv", str(venv)])
                if created is None or created.returncode:
                    return self._failed(root.name, events, "dependency_failure", "Python virtualenv creation failed")
                if os.name == "nt":
                    python_executable = str(venv / "Scripts" / "python.exe")
                    pip_executable = str(venv / "Scripts" / "pip.exe")
                else:
                    python_executable = str(venv / "bin" / "python")
                    pip_executable = str(venv / "bin" / "pip")
                installed = await asyncio.to_thread(
                    run,
                    "dependency_resolution",
                    [pip_executable, "install", "--disable-pip-version-check", "--no-input", "-r", "requirements.lock"],
                    cwd=source / "backend",
                    timeout=180,
                )
                if installed is None or installed.returncode:
                    return self._failed(root.name, events, "dependency_failure", "Python dependency installation failed")
            if any(item.ecosystem == "npm" for item in submission.dependencies):
                if not shutil.which("npm"):
                    raise SandboxUnavailable("npm is not available in the test runner")
                installed = await asyncio.to_thread(
                    run,
                    "dependency_resolution",
                    ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                    cwd=source / "frontend",
                    timeout=180,
                )
                if installed is None or installed.returncode:
                    return self._failed(root.name, events, "dependency_failure", "npm dependency installation failed")

        analysis = await asyncio.to_thread(
            run,
            "static_analysis",
            [python_executable, "-m", "compileall", "-q", "backend", "workers", "tests"],
        )
        if analysis is None:
            return self._failed(root.name, events, "resource_violation", "Static analysis exceeded resource policy")
        if analysis.returncode:
            return self._failed(root.name, events, "build_failure", "Python static analysis failed")

        if manifest.execution.frontend == "npm-build":
            if not shutil.which("npm"):
                raise SandboxUnavailable("npm is not available in the test runner")
            lint = await asyncio.to_thread(run, "static_analysis", ["npm", "run", "lint", "--if-present"], cwd=source / "frontend")
            if lint is None or lint.returncode:
                return self._failed(root.name, events, "build_failure", "Frontend static analysis failed")
            built = await asyncio.to_thread(run, "building", ["npm", "run", "build"], cwd=source / "frontend", timeout=120)
            if built is None:
                return self._failed(root.name, events, "resource_violation", "Frontend build exceeded resource policy")
            if built.returncode:
                return self._failed(root.name, events, "build_failure", "Frontend build failed")
        else:
            events.append({"state": "building", "exitCode": 0, "message": "Static frontend requires no build step"})

        python_tests = any(item.path.startswith("tests/") and item.path.endswith(".py") for item in bundle.files)
        js_tests = any(item.path.startswith("tests/") and item.path.endswith((".js", ".mjs", ".cjs")) for item in bundle.files)
        if python_tests:
            tested = await asyncio.to_thread(
                run,
                "testing",
                [python_executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test*.py", "-v"],
            )
            if tested is None or tested.returncode:
                return self._failed(root.name, events, "test_failure", "Python tests failed")
        if js_tests:
            if not shutil.which("node"):
                raise SandboxUnavailable("Node is not available in the test runner")
            tested = await asyncio.to_thread(run, "testing", ["node", "--test", "tests"])
            if tested is None or tested.returncode:
                return self._failed(root.name, events, "test_failure", "Node tests failed")

        port = self._free_port()
        backend = subprocess.Popen(
            [python_executable, "backend/app.py", "--host", "127.0.0.1", "--port", str(port)],
            cwd=source,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        processes = [backend]
        if manifest.execution.worker == "python-cli":
            worker = subprocess.Popen(
                [python_executable, "workers/worker.py"],
                cwd=source,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            processes.append(worker)
            await asyncio.sleep(0.1)
            if worker.poll() is not None:
                self._terminate(processes)
                return self._failed(root.name, events, "runtime_crash", "Worker process exited during startup")

        url = f"http://127.0.0.1:{port}"
        healthy = False
        for _ in range(60):
            if backend.poll() is not None:
                break
            try:
                with urllib.request.urlopen(url + submission.healthCheck.path, timeout=1) as response:
                    body = response.read().decode(errors="replace")
                    healthy = response.status == submission.healthCheck.expectedStatus and (
                        not submission.healthCheck.bodyMarker
                        or submission.healthCheck.bodyMarker in body
                    )
            except Exception:
                await asyncio.sleep(0.1)
            if healthy:
                break
        if not healthy:
            self._terminate(processes)
            return self._failed(root.name, events, "health_check_failure", "Configured backend health check did not pass")

        try:
            with urllib.request.urlopen(url + "/", timeout=2) as response:
                acceptance_ok = response.status == 200
        except Exception:
            acceptance_ok = False
        if not acceptance_ok:
            self._terminate(processes)
            return self._failed(root.name, events, "acceptance_test_failure", "Full-stack preview root did not return HTTP 200")

        preview_id = "preview-" + root.name
        self.jobs[root.name] = {"root": root, "processes": processes}
        self.previews[preview_id] = {"job": root.name, "url": url}
        result = RunnerResult(
            buildSuccess=True,
            testSuccess=True,
            processStartSuccess=True,
            healthCheckSuccess=True,
            acceptanceCheckSuccess=True,
            previewAvailable=True,
            testReport={"unit": "passed", "acceptance": {"rootHttp200": True}},
            staticAnalysisReport={"profile": submission.stackId, "passed": True},
            dependencyReport={
                "dependencies": [item.model_dump(mode="json") for item in submission.dependencies],
                "installNetwork": submission.installNetwork.model_dump(mode="json"),
            },
            resourceUsage={"profile": self.isolation_profile, "limitsEnforced": "timeouts and bounded output only"},
        )
        return {
            "jobId": root.name,
            "state": "preview_ready",
            "result": result.model_dump(),
            "events": events + [{"state": "preview_ready", "message": "Full-stack reference execution passed"}],
            "preview": {"id": preview_id, "targetUrl": url},
        }

    async def status(self, job_id: str):
        return {"jobId": job_id, "state": "preview_ready" if job_id in self.jobs else "cleaned"}

    async def cancel(self, job_id: str):
        return await self.cleanup(job_id)

    async def cleanup(self, job_id: str):
        job = self.jobs.pop(job_id, None)
        if job:
            self._terminate(job["processes"])
            shutil.rmtree(job["root"], ignore_errors=True)
        return {"state": "cleaned"}

    async def stop_preview(self, preview_id: str):
        item = self.previews.pop(preview_id, None)
        return await self.cleanup(item["job"]) if item else {"state": "cleaned"}

    @staticmethod
    def _terminate(processes):
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            if process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def _failed(job_id: str, events: list[dict], classification: str, message: str):
        return {
            "jobId": job_id,
            "state": "failed",
            "result": RunnerResult(
                failureEvidence={"classification": classification, "message": message}
            ).model_dump(),
            "events": events + [{"state": "failed", "message": message}],
        }


__all__ = ["FullStackSubprocessTestRunner"]
