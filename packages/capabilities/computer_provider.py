from __future__ import annotations

import base64
import binascii
import mimetypes
from typing import Any

from packages.agent_computer import AgentComputerRunnerClient
from packages.artifacts.service import ArtifactService, artifact_json, artifact_scope_from_context
from packages.capabilities.contracts import (
    ApprovalPolicy,
    CapabilityDefinition,
    CapabilityResult,
    ExecutionMode,
)
from packages.capabilities.providers import BaseProvider


_MAX_INPUTS = 20
_MAX_TRANSPORT_BYTES = 20 * 1024 * 1024
_MAX_OUTPUTS = 20
_MAX_BASH_CHARS = 50_000


def _run_id(context) -> str | None:
    invocation = context.invocation if isinstance(context.invocation, dict) else {}
    metadata = invocation.get("metadata") if isinstance(invocation.get("metadata"), dict) else {}
    return str(metadata.get("runtime_run_id") or "").strip() or None


class AgentComputerProvider(BaseProvider):
    """General bounded computation backed by the same Railway Sandbox as Studio."""

    name = "operly_agent_computer"
    _common_input = {
        "artifact_ids": {
            "type": "array",
            "maxItems": _MAX_INPUTS,
            "items": {"type": "string", "minLength": 1, "maxLength": 36},
        },
        "output_paths": {
            "type": "array",
            "maxItems": _MAX_OUTPUTS,
            "items": {"type": "string", "minLength": 1, "maxLength": 240},
        },
        "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600},
    }
    capabilities = (
        CapabilityDefinition(
            "computer.run_python",
            "computer_run_python",
            (
                "Run bounded Python in Operly's isolated Agent Computer. Durable artifact inputs appear under "
                "/workspace/input and declared output_paths are collected from /workspace/output and saved back "
                "as scoped artifacts. The sandbox receives no production credentials and has no external network."
            ),
            {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "minLength": 1, "maxLength": 250000},
                    **_common_input,
                },
                "required": ["code"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("computer:execute", "files:process"),
            approval_policy=ApprovalPolicy.AUTO,
            execution_mode=ExecutionMode.ISOLATED_RUNNER,
            execution_timeout_seconds=650,
            reversible=True,
            category="computer",
            display_name="Run Python in Agent Computer",
            tags=frozenset({"computer", "python", "sandbox", "files", "artifacts"}),
            semantic_operations=frozenset(
                {
                    "run python",
                    "calculate with python",
                    "transform files with python",
                    "create files in sandbox",
                    "process artifacts with code",
                }
            ),
        ),
        CapabilityDefinition(
            "computer.run_command",
            "computer_run_command",
            (
                "Run one bounded argv command inside Operly's isolated Agent Computer. Inputs are scoped artifacts, "
                "outputs must be explicitly declared, production credentials are never mounted, and network egress is blocked."
            ),
            {
                "type": "object",
                "properties": {
                    "argv": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 64,
                        "items": {"type": "string", "maxLength": 2000},
                    },
                    **_common_input,
                },
                "required": ["argv"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("computer:execute", "files:process"),
            approval_policy=ApprovalPolicy.AUTO,
            execution_mode=ExecutionMode.ISOLATED_RUNNER,
            execution_timeout_seconds=650,
            reversible=True,
            category="computer",
            display_name="Run command in Agent Computer",
            tags=frozenset({"computer", "command", "sandbox", "files", "artifacts"}),
            semantic_operations=frozenset(
                {
                    "run command",
                    "execute tool in sandbox",
                    "convert files in sandbox",
                    "use ffmpeg",
                    "use poppler",
                }
            ),
        ),
        CapabilityDefinition(
            "computer.run_bash",
            "computer_run_bash",
            (
                "Run a bounded Bash script inside Operly's disposable isolated Agent Computer. Use it for temporary CLI work "
                "such as inspecting supplied artifacts, running local build/test tools, or producing declared outputs. The "
                "sandbox receives no production credentials, external network is blocked, only explicit output_paths are "
                "persisted, and the sandbox is destroyed after the invocation."
            ),
            {
                "type": "object",
                "properties": {
                    "script": {"type": "string", "minLength": 1, "maxLength": _MAX_BASH_CHARS},
                    **_common_input,
                },
                "required": ["script"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("computer:execute", "files:process"),
            approval_policy=ApprovalPolicy.AUTO,
            execution_mode=ExecutionMode.ISOLATED_RUNNER,
            execution_timeout_seconds=650,
            reversible=True,
            category="computer",
            display_name="Run Bash in Agent Computer",
            tags=frozenset({"computer", "bash", "cli", "sandbox", "files", "artifacts"}),
            semantic_operations=frozenset(
                {
                    "run bash",
                    "use command line",
                    "run cli tools",
                    "run local build commands",
                    "run local tests",
                    "process artifacts with shell tools",
                }
            ),
        ),
    )

    def __init__(self, runner: AgentComputerRunnerClient | None = None):
        self.runner = runner or AgentComputerRunnerClient()

    async def _runner_inputs(self, context, artifact_ids: list[str]) -> list[dict[str, Any]]:
        if len(artifact_ids) > _MAX_INPUTS:
            raise ValueError(f"Maximum {_MAX_INPUTS} Agent Computer input artifacts")
        service = ArtifactService(context.db)
        scope = artifact_scope_from_context(context)
        rows = await service.get_many(scope, artifact_ids, max_items=_MAX_INPUTS)
        total = sum(int(row.size_bytes or 0) for row in rows)
        if total > _MAX_TRANSPORT_BYTES:
            raise ValueError("Agent Computer input transport exceeds 20 MiB; use files.batch_process for large-N data")
        output = []
        for row in rows:
            raw = await service.read_bytes(scope, row.id)
            output.append(
                {
                    "artifactId": row.id,
                    "filename": row.filename,
                    "contentType": row.content_type,
                    "contentBase64": base64.b64encode(raw).decode(),
                }
            )
        return output

    async def _persist_outputs(self, context, outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        service = ArtifactService(context.db)
        scope = artifact_scope_from_context(context)
        persisted = []
        for item in outputs[:_MAX_OUTPUTS]:
            encoded = str(item.get("contentBase64") or "")
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as error:
                raise RuntimeError("Agent Computer returned invalid output bytes") from error
            relative = str(item.get("path") or "output.bin")
            filename = relative.rsplit("/", 1)[-1] or "output.bin"
            content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            row = await service.create_bytes(
                scope,
                filename=filename,
                content_type=content_type,
                content=raw,
                source="agent_computer",
                created_by=context.actor_id,
                run_id=_run_id(context),
                metadata={"sandbox_path": relative, "isolation": "railway_sandbox_vm_v1"},
            )
            persisted.append(artifact_json(row))
        return persisted

    async def execute(self, context, capability_name: str, arguments: dict[str, Any]) -> CapabilityResult:
        supported = {"computer.run_python", "computer.run_command", "computer.run_bash"}
        if capability_name not in supported:
            return CapabilityResult(False, False, {"reason": "unsupported_computer_capability"})
        try:
            artifact_ids = [str(item) for item in arguments.get("artifact_ids") or []]
            output_paths = [str(item) for item in arguments.get("output_paths") or []]
            payload: dict[str, Any] = {
                "mode": "python" if capability_name == "computer.run_python" else "command",
                "inputs": await self._runner_inputs(context, artifact_ids),
                "outputPaths": output_paths,
                "timeoutSeconds": int(arguments.get("timeout_seconds") or 120),
            }
            if capability_name == "computer.run_python":
                payload["code"] = str(arguments.get("code") or "")
            elif capability_name == "computer.run_bash":
                script = str(arguments.get("script") or "")
                if not script.strip() or len(script) > _MAX_BASH_CHARS:
                    raise ValueError("Bash script is required and bounded")
                # The runner already executes argv as uid 10001 inside an isolated,
                # disposable sandbox. Bash is an ergonomic projection over that same
                # security boundary, not a new shell on the Operly control plane.
                payload["argv"] = ["bash", "-lc", script]
            else:
                payload["argv"] = [str(item) for item in arguments.get("argv") or []]
            response = await self.runner.execute(payload)
            generated = await self._persist_outputs(context, list(response.get("outputs") or []))
            evidence = {
                "exit_code": response.get("exitCode"),
                "timed_out": bool(response.get("timedOut")),
                "stdout": str(response.get("stdout") or "")[-12000:],
                "stderr": str(response.get("stderr") or "")[-12000:],
                "input_artifact_ids": artifact_ids,
                "artifact_ids": [item["artifact_id"] for item in generated],
                "artifacts": generated,
                "isolation": response.get("isolation"),
                "network": response.get("network"),
                "ephemeral": True,
                "side_effects": False,
            }
            return CapabilityResult(bool(response.get("ok")), bool(generated), evidence, generated[0]["artifact_id"] if generated else None)
        except (LookupError, ValueError, RuntimeError) as error:
            return CapabilityResult(False, False, {"reason": "agent_computer_failed", "message": str(error)[:1000]})

    async def verify(self, context, capability_name: str, arguments: dict[str, Any], result: CapabilityResult) -> CapabilityResult:
        del arguments
        if capability_name not in {"computer.run_python", "computer.run_command", "computer.run_bash"} or not result.success:
            return CapabilityResult(False, result.changed, result.evidence)
        ids = list(result.evidence.get("artifact_ids") or [])
        if ids:
            try:
                await ArtifactService(context.db).get_many(artifact_scope_from_context(context), ids, max_items=_MAX_OUTPUTS)
            except (LookupError, ValueError):
                return CapabilityResult(False, result.changed, {"reason": "computer_output_artifact_missing"})
        valid = (
            result.evidence.get("exit_code") == 0
            and not result.evidence.get("timed_out")
            and result.evidence.get("isolation") == "railway_sandbox_vm_v1"
            and result.evidence.get("network") == "isolated"
        )
        return CapabilityResult(
            bool(valid),
            result.changed,
            {
                "exit_code": result.evidence.get("exit_code"),
                "artifact_ids": ids,
                "isolation": result.evidence.get("isolation"),
                "network": result.evidence.get("network"),
                "ephemeral": True,
                "verified": bool(valid),
            },
            result.external_reference,
        )
