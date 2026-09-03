from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.artifacts import ArtifactScope, ArtifactService
from packages.kernel.contracts import CapabilityExecutionResult, CapabilitySpec
from packages.plugins.contracts import PluginExecutionMode
from packages.plugins.runtime_provider import (
    PluginRuntimeProvider,
    PluginRuntimeTransportError,
)
from packages.security.execution_context import ExecutionContext
from packages.workspace_modules.agent_computer.sandbox import (
    ComputerRunnerClient,
    ComputerRunnerError,
)


_EXTRACT_VALIDATED_ZIP = r'''
import json
import pathlib
import shutil
import stat
import zipfile

archive = pathlib.Path('/workspace/work/validated.zip')
root = pathlib.Path('/workspace/work/src')
max_files = 5000
max_unpacked = 200 * 1024 * 1024

if not archive.is_file() or not zipfile.is_zipfile(archive):
    raise ValueError('validated plugin artifact is not a ZIP archive')
shutil.rmtree(root, ignore_errors=True)
root.mkdir(parents=True, exist_ok=True)
count = 0
total = 0
with zipfile.ZipFile(archive, 'r') as zf:
    for info in zf.infolist():
        raw_name = str(info.filename or '').replace('\\', '/')
        rel = pathlib.PurePosixPath(raw_name)
        if not raw_name or rel.is_absolute() or '..' in rel.parts:
            raise ValueError('validated plugin artifact contains an unsafe path')
        mode = (info.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            raise ValueError('validated plugin artifact contains a symbolic link')
        if info.is_dir():
            (root / pathlib.Path(*rel.parts)).mkdir(parents=True, exist_ok=True)
            continue
        count += 1
        total += int(info.file_size)
        if count > max_files or total > max_unpacked:
            raise ValueError('validated plugin artifact exceeds runtime extraction policy')
        target = root / pathlib.Path(*rel.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info, 'r') as source, target.open('wb') as sink:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                sink.write(chunk)

entrypoint = root / 'operly_runtime.py'
if not entrypoint.is_file():
    raise ValueError('sandbox_job plugin requires operly_runtime.py at package root')
print(json.dumps({'files': count, 'unpacked_bytes': total, 'entrypoint': 'operly_runtime.py'}))
'''


_TERMINAL_EXEC_NOT_READY = "tcp-proxy exec WebSocket connection failed. (HTTP 400)"


def _execution_concurrency_limit() -> int:
    """Bound simultaneous fresh Sandbox VMs created by one API process.

    Sandbox jobs are intentionally ephemeral. A large request burst can otherwise
    ask the shared Sandbox Runner / Railway tcp-proxy to establish many fresh
    sessions and exec WebSockets at the same instant. The admission gate keeps
    requests concurrent at the Operly boundary while applying backpressure before
    provisioning. It is configurable for future capacity tuning without changing
    plugin contracts.
    """

    raw = os.getenv("OPERLY_SANDBOX_JOB_MAX_CONCURRENCY", "4").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 4
    return max(1, min(value, 32))


class SandboxJobPluginRuntimeProvider(PluginRuntimeProvider):
    """Plugin provider that adds safe, network-off ephemeral sandbox jobs.

    The entrypoint is fixed by Operly: ``operly_runtime.py`` reads one JSON invocation
    object from stdin and writes one JSON response object to stdout. Plugin manifests
    cannot supply shell commands. Each invocation gets a fresh Railway Sandbox VM,
    no private Operly network, and no provider/Workspace credential material.
    """

    def __init__(self, runner: ComputerRunnerClient | None = None) -> None:
        super().__init__()
        self.runner = runner or ComputerRunnerClient()
        self._execution_gate = asyncio.Semaphore(_execution_concurrency_limit())

    async def is_available(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
    ) -> bool:
        try:
            _, _, manifest, instance = await self._resolve(
                db,
                context=context,
                capability=capability,
                require_fresh=False,
            )
        except (LookupError, PermissionError, RuntimeError, ValueError):
            return False
        if manifest.execution_mode is PluginExecutionMode.SANDBOX_JOB:
            return bool(
                instance.provider == "railway-sandbox-job"
                and instance.artifact_id
                and instance.state == "ready"
                and instance.health_state == "healthy"
            )
        return await super().is_available(db, context=context, capability=capability)

    @staticmethod
    def _sandbox_contract(manifest) -> None:
        if manifest.runtime is None or manifest.runtime.kind != "job":
            raise PluginRuntimeTransportError("sandbox_job runtime contract is invalid")
        if manifest.runtime.network.mode != "off" or manifest.runtime.network.allowed_hosts:
            raise PermissionError(
                "sandbox_job execution is network-off until host-filtered sandbox egress is available"
            )
        if manifest.credentials:
            raise PermissionError(
                "sandbox_job plugins cannot request credentials while their sandbox network is off"
            )
        if manifest.requested_bindings:
            raise PermissionError(
                "sandbox_job plugins cannot request Workspace capability bindings until a filtered callback channel is available"
            )

    async def _terminal_exec_with_readiness_retry(
        self,
        runtime_id: str,
        *,
        max_runtime: int,
    ) -> dict[str, Any]:
        """Retry only Railway's signed tcp-proxy exec-readiness race.

        At this point the sandbox already exists and Operly has successfully
        imported, extracted, and written the invocation artifact. Railway can
        briefly return a signed HTTP 400 while the sandbox's exec WebSocket proxy
        finishes becoming ready. Reusing the same sandbox is safe here because a
        failed WebSocket connection means the terminal command was not submitted.
        No other 400, timeout, transport failure, or plugin error is retried.
        """

        arguments = {
            "command": "python3 operly_runtime.py < ../.operly/invocation.json",
            "cwd": "src",
            "timeout_seconds": max_runtime,
            "background": False,
        }
        timeout_seconds = min(max_runtime + 45, 930)
        for attempt in range(1, 5):
            try:
                return await self.runner.tool(
                    runtime_id,
                    "terminal.exec",
                    arguments,
                    timeout_seconds=timeout_seconds,
                )
            except ComputerRunnerError as error:
                if str(error) != _TERMINAL_EXEC_NOT_READY or attempt >= 4:
                    raise
                await asyncio.sleep(0.75 * attempt)
        raise ComputerRunnerError("Sandbox terminal exec readiness retries exhausted")

    async def _execute_sandbox_job(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
        arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        installation, version, manifest, instance = await self._resolve(
            db,
            context=context,
            capability=capability,
            require_fresh=False,
        )
        self._sandbox_contract(manifest)
        if instance.provider != "railway-sandbox-job" or not instance.artifact_id:
            raise PluginRuntimeTransportError("sandbox_job runtime is not reconciled")
        artifacts = ArtifactService(db)
        scope = ArtifactScope(
            "workspace",
            installation.tenant_id,
            tenant_id=installation.tenant_id,
        )
        artifact = await artifacts.get(scope, instance.artifact_id)
        validated_bytes = await artifacts.read_bytes(scope, artifact.id)
        if artifact.sha256 != hashlib.sha256(validated_bytes).hexdigest():
            raise PluginRuntimeTransportError("validated sandbox_job artifact digest mismatch")

        invocation = {
            "schema": "operly.sandbox-job-invocation/v1",
            "invocation_id": f"sj_{hashlib.sha256((installation.id + capability.id + datetime.utcnow().isoformat()).encode()).hexdigest()[:24]}",
            "capability_id": capability.id,
            "capability_version": capability.version,
            "arguments": arguments,
            "context": minimum_context,
        }
        raw_invocation = json.dumps(
            invocation,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        if len(raw_invocation) > 2 * 1024 * 1024:
            raise ValueError("sandbox_job invocation exceeds 2 MiB")

        runtime_id: str | None = None
        max_runtime = min(int(manifest.runtime.resources.max_runtime_seconds), 900)
        try:
            started = await self.runner.start(
                computer_session_id=f"plugin-job-{installation.id[:12]}-{hashlib.sha256(raw_invocation).hexdigest()[:12]}",
                workspace_id=installation.tenant_id,
                principal_id=context.user_id or context.principal_id,
                profile="coding",
                ttl_seconds=max(120, min(max_runtime + 120, 1800)),
                network_policy="off",
            )
            runtime_id = str(started.get("session_id") or started.get("id") or "").strip()
            if not runtime_id:
                raise PluginRuntimeTransportError("Sandbox Runner returned no runtime ID")
            imported = await self.runner.tool(
                runtime_id,
                "artifact.import",
                {
                    "path": "validated.zip",
                    "content_base64": base64.b64encode(validated_bytes).decode("ascii"),
                    "content_type": "application/zip",
                },
            )
            if str(imported.get("sha256") or "").lower() != artifact.sha256.lower():
                raise PluginRuntimeTransportError("Sandbox imported a different validated artifact")
            extracted = await self.runner.tool(
                runtime_id,
                "python.exec",
                {"code": _EXTRACT_VALIDATED_ZIP, "cwd": ".", "timeout_seconds": 120},
            )
            if int(extracted.get("exit_code") or 0) != 0:
                raise PluginRuntimeTransportError(
                    "sandbox_job validated artifact extraction failed: "
                    + str(extracted.get("stderr") or extracted.get("stdout") or "unknown error")[:2000]
                )
            await self.runner.tool(
                runtime_id,
                "files.write",
                {
                    "path": ".operly/invocation.json",
                    "content": raw_invocation.decode("utf-8"),
                    "append": False,
                },
            )
            packet = await self._terminal_exec_with_readiness_retry(
                runtime_id,
                max_runtime=max_runtime,
            )
            if packet.get("timed_out"):
                raise PluginRuntimeTransportError("sandbox_job execution timed out")
            if int(packet.get("exit_code") or 0) != 0:
                raise PluginRuntimeTransportError(
                    "sandbox_job execution failed: "
                    + str(packet.get("stderr") or "unknown error")[-4000:]
                )
            stdout = str(packet.get("stdout") or "")
            if len(stdout.encode("utf-8")) > 2 * 1024 * 1024:
                raise PluginRuntimeTransportError("sandbox_job response exceeds 2 MiB")
            try:
                response = json.loads(stdout or "{}")
            except json.JSONDecodeError as error:
                raise PluginRuntimeTransportError(
                    "sandbox_job entrypoint returned invalid JSON"
                ) from error
            if not isinstance(response, dict) or not isinstance(response.get("result"), dict):
                raise PluginRuntimeTransportError(
                    "sandbox_job response must contain an object result"
                )
            event_payload = response.get("event_payload") or {}
            if not isinstance(event_payload, dict):
                raise PluginRuntimeTransportError("sandbox_job event_payload must be an object")
            return CapabilityExecutionResult(
                value=dict(response["result"]),
                resource_type=(
                    str(response["resource_type"])[:120]
                    if response.get("resource_type") is not None
                    else None
                ),
                resource_id=(
                    str(response["resource_id"])[:200]
                    if response.get("resource_id") is not None
                    else None
                ),
                event_payload=dict(event_payload),
            )
        except ComputerRunnerError as error:
            raise PluginRuntimeTransportError(str(error)) from error
        finally:
            if runtime_id:
                try:
                    await self.runner.stop(runtime_id)
                except Exception:
                    pass

    async def execute(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
        arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        _, _, manifest, _ = await self._resolve(
            db,
            context=context,
            capability=capability,
            require_fresh=False,
        )
        if manifest.execution_mode is PluginExecutionMode.SANDBOX_JOB:
            async with self._execution_gate:
                return await self._execute_sandbox_job(
                    db,
                    context=context,
                    capability=capability,
                    arguments=arguments,
                    minimum_context=minimum_context,
                )
        return await super().execute(
            db,
            context=context,
            capability=capability,
            arguments=arguments,
            minimum_context=minimum_context,
        )


sandbox_job_plugin_runtime_provider = SandboxJobPluginRuntimeProvider()

__all__ = [
    "SandboxJobPluginRuntimeProvider",
    "sandbox_job_plugin_runtime_provider",
]
