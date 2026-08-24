"""Docker-backed OS isolation for the production runner gateway.

This module is intended for a *dedicated runner host*, never the Operly API host.
The trusted gateway owns the Docker socket. Generated software never receives that
socket, host mounts, Operly credentials, database credentials, or another job's
network namespace.
"""
from __future__ import annotations

import io
import json
import os
import re
import tarfile
import time
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

import docker
from docker.errors import DockerException, ImageNotFound, NotFound
from docker.types import Ulimit

from packages.custom_software.runner_contracts import BuildSubmission, RunnerResult
from packages.custom_software.source_bundles import SourceBundle
from packages.relational_data.contracts import RELATIONAL_CAPABILITY_ID, RelationalMigration
from packages.runtime_plugins import (
    FULLSTACK_PROFILE_VERSION,
    FULLSTACK_RUNTIME_ID,
    parse_fullstack_manifest,
    validate_fullstack_source,
)
from packages.runtime_plugins.relational_source_validation import validate_relational_source


class IsolationUnavailable(RuntimeError):
    pass


class IsolationFailure(RuntimeError):
    pass


class JobCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class ExecutionOutcome:
    response: dict
    resources: dict
    preview_upstream: str | None = None


class DockerIsolationBackend:
    """Execute one Solution in containers isolated from the gateway and other jobs."""

    implementation = "docker_per_job_v1"
    isolation_profile = "dedicated_host_container_per_job"

    def __init__(self):
        self.client = docker.from_env()
        self.job_image = os.getenv("OPERLY_RUNNER_JOB_IMAGE", "operly-runner-job:1")
        self.proxy_image = os.getenv("OPERLY_RUNNER_PROXY_IMAGE", "operly-runner-proxy:1")
        self.control_network = os.getenv(
            "OPERLY_RUNNER_CONTROL_NETWORK", "operly-runner-control"
        )
        self.egress_network = os.getenv("OPERLY_RUNNER_EGRESS_NETWORK", "bridge")
        self.binding_hosts = {
            item.strip().lower().rstrip(".")
            for item in os.getenv("OPERLY_RUNNER_BINDING_HOSTS", "").split(",")
            if item.strip()
        }
        self.allow_http_bindings = os.getenv("OPERLY_RUNNER_ALLOW_HTTP_BINDINGS") == "1"
        self._verify_host()

    def _verify_host(self) -> None:
        try:
            self.client.ping()
            self.client.images.get(self.job_image)
            self.client.images.get(self.proxy_image)
            self.client.networks.get(self.control_network)
            self.client.networks.get(self.egress_network)
        except (DockerException, ImageNotFound, NotFound) as error:
            raise IsolationUnavailable(
                "Docker runner host is not ready: Docker daemon, trusted images, "
                "control network and egress network must exist"
            ) from error

    def capabilities(self) -> dict:
        self._verify_host()
        return {
            "protocolVersion": 2,
            "isolation": self.isolation_profile,
            "profiles": {
                FULLSTACK_RUNTIME_ID: {
                    "profileVersion": FULLSTACK_PROFILE_VERSION,
                    "supportsPreview": True,
                    "supportsDeploy": False,
                }
            },
        }

    @staticmethod
    def _safe_name(value: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-.")
        return cleaned[:48] or "job"

    def _binding_container_name(self, short: str, semantic_name: str) -> str:
        semantic = self._safe_name(semantic_name).lower()[:24]
        return f"operly-binding-{short}-{semantic}"

    def _binding_file_rows(self, submission: BuildSubmission, short: str) -> list[dict]:
        rows: list[dict] = []
        for binding in submission.serviceBindings:
            if binding.transport is not None and binding.capabilityId != RELATIONAL_CAPABILITY_ID:
                raise IsolationFailure("Runner transport is unsupported for this capability")
            row = {
                "semanticName": binding.semanticName,
                "capabilityId": binding.capabilityId,
                "required": binding.required,
            }
            if binding.capabilityId == RELATIONAL_CAPABILITY_ID:
                if binding.transport is None:
                    raise IsolationFailure("Relational binding is missing runner transport authorization")
                row["endpoint"] = (
                    f"http://{self._binding_container_name(short, binding.semanticName)}:8083"
                )
            rows.append(row)
        return rows

    def _archive(self, bundle: SourceBundle, submission: BuildSubmission, short: str) -> bytes:
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w") as archive:
            for item in bundle.files:
                info = tarfile.TarInfo(item.path)
                info.size = len(item.content)
                info.mode = 0o644
                info.uid = 10001
                info.gid = 10001
                archive.addfile(info, io.BytesIO(item.content))
            # Transport grants are runner-only. The generated runtime receives only
            # semantic identity plus a local sidecar endpoint.
            bindings = json.dumps(
                self._binding_file_rows(submission, short),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            info = tarfile.TarInfo(".operly-bindings.json")
            info.size = len(bindings)
            info.mode = 0o444
            info.uid = 10001
            info.gid = 10001
            archive.addfile(info, io.BytesIO(bindings))
        return payload.getvalue()

    @staticmethod
    def _relational_migrations(bundle: SourceBundle) -> list[RelationalMigration]:
        migrations: list[RelationalMigration] = []
        for item in bundle.files:
            if not item.path.startswith("migrations/") or item.path == "migrations/README.md":
                continue
            if not item.path.endswith(".json"):
                raise IsolationFailure(f"Relational migration must be JSON: {item.path}")
            try:
                raw = json.loads(item.content.decode("utf-8"))
                migrations.append(RelationalMigration.model_validate(raw))
            except Exception as error:
                raise IsolationFailure(f"Relational migration is invalid: {item.path}") from error
        return sorted(migrations, key=lambda item: item.version)

    def _validated_binding_url(self, value: str) -> str:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise IsolationFailure("Relational binding gateway URL is invalid")
        if parsed.scheme != "https" and not self.allow_http_bindings:
            raise IsolationFailure("Relational binding gateway must use HTTPS")
        if not self.binding_hosts or host not in self.binding_hosts:
            raise IsolationFailure("Relational binding gateway host is not runner-allowlisted")
        return value.rstrip("/")

    def _apply_relational_migrations(
        self,
        submission: BuildSubmission,
        bundle: SourceBundle,
    ) -> dict:
        bindings = [
            item for item in submission.serviceBindings if item.capabilityId == RELATIONAL_CAPABILITY_ID
        ]
        migrations = self._relational_migrations(bundle)
        if migrations and not bindings:
            raise IsolationFailure("Relational migrations require a data.relational binding")
        if not bindings:
            return {"configured": False, "appliedVersions": []}
        if len(bindings) != 1:
            raise IsolationFailure("Exactly one relational binding is supported per Solution")
        transport = bindings[0].transport
        if transport is None or not transport.migrationToken:
            raise IsolationFailure("Relational migration authorization is unavailable")
        gateway = self._validated_binding_url(transport.gatewayUrl)
        if not migrations:
            return {"configured": True, "appliedVersions": []}
        body = json.dumps(
            {"migrations": [item.model_dump(mode="json") for item in migrations]},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        request = urllib.request.Request(
            gateway + "/api/runtime/relational/migrations/apply",
            data=body,
            headers={
                "Authorization": f"Bearer {transport.migrationToken}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read(1024 * 1024).decode("utf-8"))
                if response.status not in range(200, 300):
                    raise IsolationFailure("Relational migration gateway rejected the migration")
        except IsolationFailure:
            raise
        except Exception as error:
            raise IsolationFailure("Relational migration gateway request failed") from error
        return {
            "configured": True,
            "currentVersion": payload.get("currentVersion"),
            "appliedVersions": payload.get("appliedVersions") or [],
        }

    def _start_binding_proxies(
        self,
        submission: BuildSubmission,
        network,
        labels: dict,
        short: str,
    ) -> list:
        proxies = []
        try:
            for binding in submission.serviceBindings:
                if binding.capabilityId != RELATIONAL_CAPABILITY_ID:
                    continue
                transport = binding.transport
                if transport is None:
                    raise IsolationFailure("Relational runtime authorization is unavailable")
                gateway = self._validated_binding_url(transport.gatewayUrl)
                proxy = self.client.containers.run(
                    self.proxy_image,
                    detach=True,
                    name=self._binding_container_name(short, binding.semanticName),
                    network=network.name,
                    environment={
                        "OPERLY_PROXY_MODE": "binding",
                        "OPERLY_PROXY_PORT": "8083",
                        "OPERLY_PROXY_BINDING_TARGET": gateway,
                        "OPERLY_PROXY_BINDING_TOKEN": transport.runtimeToken,
                        "OPERLY_PROXY_BINDING_PREFIX": "/api/runtime/relational",
                    },
                    labels=labels,
                    mem_limit="96m",
                    pids_limit=64,
                    cap_drop=["ALL"],
                    security_opt=["no-new-privileges:true"],
                    read_only=True,
                    tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"},
                )
                self.client.networks.get(self.egress_network).connect(proxy)
                proxies.append(proxy)
        except Exception:
            for proxy in proxies:
                try:
                    proxy.remove(force=True)
                except (DockerException, NotFound):
                    pass
            raise
        return proxies

    def _event(
        self,
        events: list[dict],
        callback: Callable[[dict], None],
        state: str,
        message: str,
        exit_code: int | None = None,
    ) -> None:
        row = {"state": state, "message": message}
        if exit_code is not None:
            row["exitCode"] = exit_code
        events.append(row)
        callback(row)

    @staticmethod
    def _ensure_not_cancelled(cancelled: Callable[[], bool]) -> None:
        if cancelled():
            raise JobCancelled("Build was cancelled")

    def _exec(
        self,
        container,
        command: list[str],
        *,
        workdir: str,
        timeout_seconds: int,
        log_limit: int,
    ) -> tuple[int, str]:
        wrapped = [
            "timeout",
            "--signal=KILL",
            f"{max(1, int(timeout_seconds))}s",
            *command,
        ]
        created = self.client.api.exec_create(
            container.id,
            wrapped,
            workdir=workdir,
            user="10001:10001",
        )
        exec_id = created["Id"]
        output = bytearray()
        stream = self.client.api.exec_start(exec_id, stream=True, demux=False)
        for chunk in stream:
            if not chunk:
                continue
            output.extend(chunk)
            if len(output) > log_limit:
                try:
                    container.kill()
                except DockerException:
                    pass
                raise IsolationFailure("Command log output exceeded resource policy")
        inspected = self.client.api.exec_inspect(exec_id)
        code = int(inspected.get("ExitCode") or 0)
        text = bytes(output[-min(log_limit, 4000) :]).decode(errors="replace")
        return code, text

    def _start_exec(self, container, command: list[str], workdir: str) -> str:
        created = self.client.api.exec_create(
            container.id,
            command,
            workdir=workdir,
            user="10001:10001",
        )
        exec_id = created["Id"]
        self.client.api.exec_start(exec_id, detach=True)
        return exec_id

    def _failed(
        self,
        job_id: str,
        events: list[dict],
        classification: str,
        message: str,
    ) -> ExecutionOutcome:
        return ExecutionOutcome(
            response={
                "jobId": job_id,
                "state": "failed",
                "result": RunnerResult(
                    failureEvidence={
                        "classification": classification,
                        "message": message,
                    }
                ).model_dump(),
                "events": events + [{"state": "failed", "message": message}],
            },
            resources={},
        )

    def _container_security(self, submission: BuildSubmission, *, readonly: bool) -> dict:
        return {
            "mem_limit": f"{submission.resources.memoryMb}m",
            "nano_cpus": int(submission.resources.cpu * 1_000_000_000),
            "pids_limit": submission.resources.processes,
            "ulimits": [
                Ulimit(
                    name="nofile",
                    soft=submission.resources.openFiles,
                    hard=submission.resources.openFiles,
                )
            ],
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "read_only": readonly,
            "user": "10001:10001",
            "working_dir": "/workspace",
            "init": True,
        }

    def run_job(
        self,
        submission: BuildSubmission,
        bundle: SourceBundle,
        *,
        event_callback: Callable[[dict], None] = lambda _event: None,
        cancelled: Callable[[], bool] = lambda: False,
        job_id: str | None = None,
    ) -> ExecutionOutcome:
        job_id = job_id or uuid.uuid4().hex
        short = self._safe_name(job_id)[:20]
        labels = {
            "operly.runner.managed": "true",
            "operly.runner.job": job_id,
        }
        events: list[dict] = []
        network = build_container = runtime_container = None
        egress_proxy = preview_proxy = None
        binding_proxies: list = []
        runtime_image_tag = None
        resources: dict = {}
        success = False
        migration_report = {"configured": False, "appliedVersions": []}
        try:
            self._verify_host()
            if submission.stackId != FULLSTACK_RUNTIME_ID:
                raise IsolationFailure("Runner only accepts operly-fullstack-v1")
            if submission.stackVersion != FULLSTACK_PROFILE_VERSION:
                raise IsolationFailure("Runner profile version mismatch")
            validation = validate_fullstack_source(bundle)
            relational_validation = validate_relational_source(bundle)
            errors = tuple((*validation.errors, *relational_validation.errors))
            if not validation.valid or not relational_validation.valid:
                return self._failed(
                    job_id,
                    events,
                    "security_policy_violation",
                    "; ".join(errors),
                )
            manifest = parse_fullstack_manifest(bundle)
            self._ensure_not_cancelled(cancelled)

            # Validate runner-only transport before staging. _archive then writes a
            # deliberately redacted binding file containing only local endpoints.
            self._binding_file_rows(submission, short)

            network = self.client.networks.create(
                f"operly-job-{short}-{uuid.uuid4().hex[:6]}",
                driver="bridge",
                internal=True,
                labels=labels,
            )
            resources["network"] = network.name
            self._event(events, event_callback, "provisioning", "Created isolated job network")

            proxy_url = None
            if submission.dependencies:
                if submission.installNetwork.mode != "dependency_registry_only":
                    raise IsolationFailure(
                        "Dependency-bearing jobs require dependency_registry_only network policy"
                    )
                allow_hosts: set[str] = set()
                if any(item.ecosystem == "python" for item in submission.dependencies):
                    allow_hosts.update({"pypi.org", "files.pythonhosted.org"})
                if any(item.ecosystem == "npm" for item in submission.dependencies):
                    allow_hosts.add("registry.npmjs.org")
                egress_proxy = self.client.containers.run(
                    self.proxy_image,
                    detach=True,
                    name=f"operly-egress-{short}-{uuid.uuid4().hex[:6]}",
                    network=network.name,
                    environment={
                        "OPERLY_PROXY_MODE": "egress",
                        "OPERLY_PROXY_PORT": "8081",
                        "OPERLY_PROXY_ALLOW_HOSTS": ",".join(sorted(allow_hosts)),
                    },
                    labels=labels,
                    mem_limit="96m",
                    pids_limit=64,
                    cap_drop=["ALL"],
                    security_opt=["no-new-privileges:true"],
                    read_only=True,
                    tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"},
                )
                self.client.networks.get(self.egress_network).connect(egress_proxy)
                resources["egressProxy"] = egress_proxy.id
                proxy_url = f"http://{egress_proxy.name}:8081"
                self._event(
                    events,
                    event_callback,
                    "dependency_resolution",
                    "Registry-only dependency egress enabled",
                )

            environment = {
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
                "HOME": "/home/operly",
                "OPERLY_BINDINGS_FILE": "/workspace/.operly-bindings.json",
                "NO_PROXY": "localhost,127.0.0.1",
            }
            if proxy_url:
                environment.update(
                    {
                        "HTTP_PROXY": proxy_url,
                        "HTTPS_PROXY": proxy_url,
                        "http_proxy": proxy_url,
                        "https_proxy": proxy_url,
                        "PIP_INDEX_URL": "https://pypi.org/simple",
                        "NPM_CONFIG_REGISTRY": "https://registry.npmjs.org/",
                    }
                )
            build_container = self.client.containers.run(
                self.job_image,
                detach=True,
                name=f"operly-build-{short}-{uuid.uuid4().hex[:6]}",
                network=network.name,
                environment=environment,
                labels=labels,
                **self._container_security(submission, readonly=False),
            )
            resources["buildContainer"] = build_container.id
            if not build_container.put_archive(
                "/workspace", self._archive(bundle, submission, short)
            ):
                raise IsolationFailure("Unable to stage source bundle")
            self._event(events, event_callback, "source_staging", "Staged immutable source bundle")
            self._ensure_not_cancelled(cancelled)

            python_executable = "python"
            if any(item.ecosystem == "python" for item in submission.dependencies):
                code, output = self._exec(
                    build_container,
                    ["python", "-m", "venv", "/workspace/.venv"],
                    workdir="/workspace",
                    timeout_seconds=60,
                    log_limit=submission.resources.logBytes,
                )
                if code:
                    return self._failed(
                        job_id, events, "dependency_failure", output or "venv creation failed"
                    )
                python_executable = "/workspace/.venv/bin/python"
                code, output = self._exec(
                    build_container,
                    [
                        "/workspace/.venv/bin/pip",
                        "install",
                        "--disable-pip-version-check",
                        "--no-input",
                        "--only-binary=:all:",
                        "-r",
                        "requirements.lock",
                    ],
                    workdir="/workspace/backend",
                    timeout_seconds=min(240, submission.maxDurationSeconds),
                    log_limit=submission.resources.logBytes,
                )
                if code:
                    return self._failed(
                        job_id,
                        events,
                        "dependency_failure",
                        output or "Python dependency installation failed",
                    )
            if any(item.ecosystem == "npm" for item in submission.dependencies):
                code, output = self._exec(
                    build_container,
                    ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                    workdir="/workspace/frontend",
                    timeout_seconds=min(240, submission.maxDurationSeconds),
                    log_limit=submission.resources.logBytes,
                )
                if code:
                    return self._failed(
                        job_id,
                        events,
                        "dependency_failure",
                        output or "npm dependency installation failed",
                    )

            if egress_proxy is not None:
                try:
                    egress_proxy.remove(force=True)
                finally:
                    egress_proxy = None
                    resources.pop("egressProxy", None)
                self._event(
                    events,
                    event_callback,
                    "dependency_resolution",
                    "Dependency egress removed before generated code execution",
                )

            self._ensure_not_cancelled(cancelled)
            code, output = self._exec(
                build_container,
                [python_executable, "-m", "compileall", "-q", "backend", "workers", "tests"],
                workdir="/workspace",
                timeout_seconds=60,
                log_limit=submission.resources.logBytes,
            )
            self._event(
                events,
                event_callback,
                "static_analysis",
                output or "Python compile check passed",
                code,
            )
            if code:
                return self._failed(job_id, events, "build_failure", "Python static analysis failed")

            if manifest.execution.frontend == "npm-build":
                code, output = self._exec(
                    build_container,
                    ["npm", "run", "lint", "--if-present"],
                    workdir="/workspace/frontend",
                    timeout_seconds=60,
                    log_limit=submission.resources.logBytes,
                )
                if code:
                    return self._failed(job_id, events, "build_failure", output or "Frontend lint failed")
                code, output = self._exec(
                    build_container,
                    ["npm", "run", "build"],
                    workdir="/workspace/frontend",
                    timeout_seconds=min(180, submission.maxDurationSeconds),
                    log_limit=submission.resources.logBytes,
                )
                self._event(events, event_callback, "building", output or "Frontend build passed", code)
                if code:
                    return self._failed(job_id, events, "build_failure", "Frontend build failed")
            else:
                self._event(
                    events,
                    event_callback,
                    "building",
                    "Static frontend requires no build command",
                    0,
                )

            self._ensure_not_cancelled(cancelled)
            python_tests = any(
                item.path.startswith("tests/") and item.path.endswith(".py")
                for item in bundle.files
            )
            node_tests = any(
                item.path.startswith("tests/") and item.path.endswith((".js", ".mjs", ".cjs"))
                for item in bundle.files
            )
            if python_tests:
                code, output = self._exec(
                    build_container,
                    [
                        python_executable,
                        "-m",
                        "unittest",
                        "discover",
                        "-s",
                        "tests",
                        "-p",
                        "test*.py",
                        "-v",
                    ],
                    workdir="/workspace",
                    timeout_seconds=min(180, submission.maxDurationSeconds),
                    log_limit=submission.resources.logBytes,
                )
                self._event(events, event_callback, "testing", output or "Python tests passed", code)
                if code:
                    return self._failed(job_id, events, "test_failure", "Python tests failed")
            if node_tests:
                code, output = self._exec(
                    build_container,
                    ["node", "--test", "tests"],
                    workdir="/workspace",
                    timeout_seconds=min(180, submission.maxDurationSeconds),
                    log_limit=submission.resources.logBytes,
                )
                self._event(events, event_callback, "testing", output or "Node tests passed", code)
                if code:
                    return self._failed(job_id, events, "test_failure", "Node tests failed")

            # Persistent schema mutation is a post-test quality gate. The migration
            # payload comes from the immutable original bundle, not the writable
            # build container, so generated tests cannot rewrite what is applied.
            self._ensure_not_cancelled(cancelled)
            migration_report = self._apply_relational_migrations(submission, bundle)
            if migration_report["configured"]:
                self._event(
                    events,
                    event_callback,
                    "migrating",
                    "Relational application schema is current",
                    0,
                )

            binding_proxies = self._start_binding_proxies(
                submission, network, labels, short
            )
            if binding_proxies:
                resources["bindingProxies"] = [proxy.id for proxy in binding_proxies]
                self._event(
                    events,
                    event_callback,
                    "binding_services",
                    "Started credential-hiding relational binding sidecar",
                    0,
                )

            self._ensure_not_cancelled(cancelled)
            runtime_image_tag = f"operly-runner-runtime:{short}-{uuid.uuid4().hex[:8]}"
            build_container.commit(
                repository=runtime_image_tag.split(":", 1)[0],
                tag=runtime_image_tag.split(":", 1)[1],
                changes=[
                    "USER 10001:10001",
                    "WORKDIR /workspace",
                    "ENV PYTHONDONTWRITEBYTECODE=1",
                    f"LABEL operly.runner.managed=true operly.runner.job={job_id}",
                ],
            )
            resources["runtimeImage"] = runtime_image_tag
            build_container.remove(force=True)
            build_container = None
            resources.pop("buildContainer", None)

            runtime_container = self.client.containers.run(
                runtime_image_tag,
                command=["sleep", "infinity"],
                detach=True,
                name=f"operly-runtime-{short}-{uuid.uuid4().hex[:6]}",
                network=network.name,
                environment={
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONUNBUFFERED": "1",
                    "HOME": "/home/operly",
                    "OPERLY_BINDINGS_FILE": "/workspace/.operly-bindings.json",
                    "NO_PROXY": "*",
                },
                labels=labels,
                tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},
                **self._container_security(submission, readonly=True),
            )
            resources["runtimeContainer"] = runtime_container.id
            backend_exec = self._start_exec(
                runtime_container,
                [
                    python_executable,
                    "backend/app.py",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8080",
                ],
                "/workspace",
            )
            resources["backendExec"] = backend_exec
            worker_exec = None
            if manifest.execution.worker == "python-cli":
                worker_exec = self._start_exec(
                    runtime_container,
                    [python_executable, "workers/worker.py"],
                    "/workspace",
                )
                resources["workerExec"] = worker_exec
            self._event(events, event_callback, "starting", "Started read-only runtime container")

            preview_proxy = self.client.containers.run(
                self.proxy_image,
                detach=True,
                name=f"operly-preview-{short}-{uuid.uuid4().hex[:6]}",
                network=network.name,
                environment={
                    "OPERLY_PROXY_MODE": "preview",
                    "OPERLY_PROXY_PORT": "8082",
                    "OPERLY_PROXY_TARGET_HOST": runtime_container.name,
                    "OPERLY_PROXY_TARGET_PORT": "8080",
                },
                labels=labels,
                mem_limit="96m",
                pids_limit=64,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                read_only=True,
                tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"},
            )
            self.client.networks.get(self.control_network).connect(preview_proxy)
            preview_proxy.reload()
            control = preview_proxy.attrs["NetworkSettings"]["Networks"].get(self.control_network)
            if not control or not control.get("IPAddress"):
                raise IsolationFailure("Preview sidecar did not join runner control network")
            preview_upstream = f"http://{control['IPAddress']}:8082"
            resources["previewProxy"] = preview_proxy.id
            resources["previewUpstream"] = preview_upstream

            health_deadline = time.monotonic() + submission.healthCheck.timeoutSeconds
            healthy = False
            while time.monotonic() < health_deadline:
                self._ensure_not_cancelled(cancelled)
                inspected = self.client.api.exec_inspect(backend_exec)
                if not inspected.get("Running", False) and inspected.get("ExitCode") not in {None, 0}:
                    break
                if worker_exec:
                    worker = self.client.api.exec_inspect(worker_exec)
                    if not worker.get("Running", False):
                        raise IsolationFailure("Worker exited during startup")
                try:
                    with urllib.request.urlopen(
                        preview_upstream + submission.healthCheck.path,
                        timeout=1,
                    ) as response:
                        body = response.read(4096).decode(errors="replace")
                        healthy = response.status == submission.healthCheck.expectedStatus and (
                            not submission.healthCheck.bodyMarker
                            or submission.healthCheck.bodyMarker in body
                        )
                except Exception:
                    time.sleep(0.15)
                if healthy:
                    break
            self._event(
                events,
                event_callback,
                "health_checking",
                "Configured health check passed" if healthy else "Configured health check failed",
                0 if healthy else 1,
            )
            if not healthy:
                raise IsolationFailure("Configured backend health check did not pass")

            try:
                with urllib.request.urlopen(preview_upstream + "/", timeout=2) as response:
                    accepted = response.status == 200
            except Exception:
                accepted = False
            self._event(
                events,
                event_callback,
                "acceptance_testing",
                "Preview root returned HTTP 200" if accepted else "Preview root acceptance failed",
                0 if accepted else 1,
            )
            if not accepted:
                raise IsolationFailure("Full-stack preview root did not return HTTP 200")

            runtime_container.reload()
            security = runtime_container.attrs.get("HostConfig", {})
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
                    "dependencies": [
                        item.model_dump(mode="json") for item in submission.dependencies
                    ],
                    "installNetwork": submission.installNetwork.model_dump(mode="json"),
                    "installEgressRemovedBeforeBuild": True,
                },
                resourceUsage={
                    "isolation": self.isolation_profile,
                    "readOnlyRuntimeRootfs": bool(security.get("ReadonlyRootfs")),
                    "capDrop": security.get("CapDrop") or [],
                    "pidsLimit": security.get("PidsLimit"),
                    "memoryBytes": security.get("Memory"),
                    "nanoCpus": security.get("NanoCpus"),
                    "network": "per-job internal bridge; trusted sidecars only",
                    "relationalData": {
                        "configured": migration_report.get("configured", False),
                        "currentVersion": migration_report.get("currentVersion"),
                        "bindingSidecars": len(binding_proxies),
                    },
                },
            )
            preview_id = "preview-" + job_id
            response = {
                "jobId": job_id,
                "state": "preview_ready",
                "result": result.model_dump(),
                "events": events + [
                    {
                        "state": "preview_ready",
                        "message": "Isolated full-stack execution passed every quality gate",
                    }
                ],
                "preview": {"id": preview_id},
            }
            success = True
            return ExecutionOutcome(response, resources, preview_upstream)
        except JobCancelled:
            return self._failed(job_id, events, "cancelled", "Build was cancelled")
        except IsolationFailure as error:
            lowered = str(error).lower()
            if "migration" in lowered or "relational" in lowered or "binding" in lowered:
                classification = "service_binding_failure"
            elif "health check" in lowered:
                classification = "health_check_failure"
            elif "worker" in lowered or "runtime" in lowered:
                classification = "runtime_crash"
            else:
                classification = "runner_infrastructure_failure"
            return self._failed(job_id, events, classification, str(error))
        except DockerException as error:
            return self._failed(job_id, events, "runner_infrastructure_failure", str(error))
        finally:
            if not success:
                self._cleanup_objects(
                    preview_proxy=preview_proxy,
                    binding_proxies=binding_proxies,
                    runtime_container=runtime_container,
                    build_container=build_container,
                    egress_proxy=egress_proxy,
                    runtime_image_tag=runtime_image_tag,
                    network=network,
                )

    def _cleanup_objects(
        self,
        *,
        preview_proxy=None,
        binding_proxies=None,
        runtime_container=None,
        build_container=None,
        egress_proxy=None,
        runtime_image_tag: str | None = None,
        network=None,
    ) -> None:
        containers = [preview_proxy, runtime_container, build_container, egress_proxy]
        containers.extend(binding_proxies or [])
        for container in containers:
            if container is None:
                continue
            try:
                container.remove(force=True)
            except (DockerException, NotFound):
                pass
        if runtime_image_tag:
            try:
                self.client.images.remove(runtime_image_tag, force=True)
            except (DockerException, ImageNotFound):
                pass
        if network is not None:
            try:
                network.remove()
            except (DockerException, NotFound):
                pass

    def cleanup(self, resources: dict) -> None:
        def container(key: str):
            value = resources.get(key)
            if not value:
                return None
            try:
                return self.client.containers.get(value)
            except NotFound:
                return None

        binding_proxies = []
        for value in resources.get("bindingProxies") or []:
            try:
                binding_proxies.append(self.client.containers.get(value))
            except NotFound:
                pass
        network = None
        if resources.get("network"):
            try:
                network = self.client.networks.get(resources["network"])
            except NotFound:
                pass
        self._cleanup_objects(
            preview_proxy=container("previewProxy"),
            binding_proxies=binding_proxies,
            runtime_container=container("runtimeContainer"),
            build_container=container("buildContainer"),
            egress_proxy=container("egressProxy"),
            runtime_image_tag=resources.get("runtimeImage"),
            network=network,
        )

    def cleanup_job_id(self, job_id: str) -> None:
        label = f"operly.runner.job={job_id}"
        for container in self.client.containers.list(all=True, filters={"label": label}):
            try:
                container.remove(force=True)
            except (DockerException, NotFound):
                pass
        for image in self.client.images.list(filters={"label": label}):
            try:
                self.client.images.remove(image.id, force=True)
            except (DockerException, ImageNotFound):
                pass
        for network in self.client.networks.list(filters={"label": label}):
            try:
                network.remove()
            except (DockerException, NotFound):
                pass

    def inspect_runtime(self, resources: dict) -> dict:
        container_id = resources.get("runtimeContainer")
        if not container_id:
            return {}
        try:
            container = self.client.containers.get(container_id)
        except NotFound:
            return {}
        container.reload()
        host = container.attrs.get("HostConfig", {})
        networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
        return {
            "containerId": container.id,
            "status": container.status,
            "readOnlyRootfs": bool(host.get("ReadonlyRootfs")),
            "capDrop": host.get("CapDrop") or [],
            "pidsLimit": host.get("PidsLimit"),
            "networks": sorted(networks),
            "bindingSidecars": len(resources.get("bindingProxies") or []),
        }


__all__ = [
    "DockerIsolationBackend",
    "ExecutionOutcome",
    "IsolationFailure",
    "IsolationUnavailable",
]
