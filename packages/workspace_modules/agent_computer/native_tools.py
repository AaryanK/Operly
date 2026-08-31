from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.agent_computer_models import AgentComputerSessionRecord, AgentComputerStepRecord
from packages.kernel.contracts import CapabilityExecutionResult, CapabilityRisk, CapabilitySpec
from packages.security.execution_context import ExecutionContext
from packages.workspace_modules.agent_computer.sandbox import ComputerRunnerClient, ComputerRunnerError


PROVIDER_ID = "operly.agent_computer"


def _object(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
    additional: bool = False,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": additional,
    }


SESSION_ID = {"type": "string", "minLength": 1, "maxLength": 80}
PATH = {"type": "string", "minLength": 1, "maxLength": 2000}
TIMEOUT = {"type": "integer", "minimum": 1, "maximum": 900}


def _capability(
    capability_id: str,
    display_name: str,
    description: str,
    input_schema: dict[str, Any],
    *,
    risk: CapabilityRisk = CapabilityRisk.LOW,
    tags: tuple[str, ...] = (),
) -> CapabilitySpec:
    return CapabilitySpec(
        id=capability_id,
        version="1.0.0",
        display_name=display_name,
        description=description,
        provider_id=PROVIDER_ID,
        scopes=frozenset({"workspace"}),
        input_schema=input_schema,
        output_schema=_object({}, additional=True),
        permissions=("computer:execute",),
        risk=risk,
        approval_required=False,
        resource_scope="workspace",
        reversible=True,
        tags=frozenset(("computer", "agent-runtime", "sandbox", *tags)),
    )


def computer_native_capabilities() -> tuple[CapabilitySpec, ...]:
    session_only = _object({"computer_session_id": SESSION_ID}, required=["computer_session_id"])
    return (
        _capability(
            "computer.runtime.start",
            "Start Agent Computer runtime",
            "Allocate the isolated compute runtime attached to an Agent Computer session.",
            _object(
                {
                    "computer_session_id": SESSION_ID,
                    "profile": {
                        "type": "string",
                        "enum": ["general", "coding", "data", "browser"],
                    },
                    "ttl_seconds": {"type": "integer", "minimum": 60, "maximum": 21600},
                    "network_policy": {"type": "string", "enum": ["off", "web", "full"]},
                },
                required=["computer_session_id"],
            ),
            tags=("runtime", "lifecycle"),
        ),
        _capability(
            "computer.runtime.status",
            "Inspect Agent Computer runtime",
            "Read the current isolated runtime state and backend-reported tool inventory.",
            session_only,
            risk=CapabilityRisk.READ_ONLY,
            tags=("runtime", "status"),
        ),
        _capability(
            "computer.runtime.stop",
            "Stop Agent Computer runtime",
            "Terminate the isolated runtime and all processes owned by the Computer session.",
            session_only,
            tags=("runtime", "lifecycle"),
        ),
        _capability(
            "computer.terminal.exec",
            "Run terminal command",
            "Execute a bounded shell command inside the isolated Agent Computer runtime.",
            _object(
                {
                    "computer_session_id": SESSION_ID,
                    "command": {"type": "string", "minLength": 1, "maxLength": 30000},
                    "cwd": {"type": "string", "maxLength": 2000},
                    "timeout_seconds": TIMEOUT,
                    "background": {"type": "boolean"},
                    "env": _object({}, additional=True),
                },
                required=["computer_session_id", "command"],
            ),
            tags=("terminal", "shell", "process"),
        ),
        _capability(
            "computer.python.exec",
            "Run Python",
            "Execute Python 3 code inside the isolated Agent Computer runtime and return stdout/stderr/exit state.",
            _object(
                {
                    "computer_session_id": SESSION_ID,
                    "code": {"type": "string", "minLength": 1, "maxLength": 120000},
                    "cwd": {"type": "string", "maxLength": 2000},
                    "timeout_seconds": TIMEOUT,
                },
                required=["computer_session_id", "code"],
            ),
            tags=("python", "code", "data"),
        ),
        _capability(
            "computer.files.list",
            "List sandbox files",
            "List files and directories inside the Agent Computer workspace.",
            _object(
                {
                    "computer_session_id": SESSION_ID,
                    "path": {"type": "string", "maxLength": 2000},
                    "recursive": {"type": "boolean"},
                    "max_entries": {"type": "integer", "minimum": 1, "maximum": 5000},
                },
                required=["computer_session_id"],
            ),
            risk=CapabilityRisk.READ_ONLY,
            tags=("filesystem", "read"),
        ),
        _capability(
            "computer.files.read",
            "Read sandbox file",
            "Read a bounded UTF-8/text file from the Agent Computer workspace.",
            _object(
                {
                    "computer_session_id": SESSION_ID,
                    "path": PATH,
                    "max_bytes": {"type": "integer", "minimum": 1, "maximum": 2000000},
                },
                required=["computer_session_id", "path"],
            ),
            risk=CapabilityRisk.READ_ONLY,
            tags=("filesystem", "read"),
        ),
        _capability(
            "computer.files.write",
            "Write sandbox file",
            "Create or replace a UTF-8/text file inside the Agent Computer workspace.",
            _object(
                {
                    "computer_session_id": SESSION_ID,
                    "path": PATH,
                    "content": {"type": "string", "maxLength": 2000000},
                    "append": {"type": "boolean"},
                },
                required=["computer_session_id", "path", "content"],
            ),
            tags=("filesystem", "write"),
        ),
        _capability(
            "computer.files.mkdir",
            "Create sandbox directory",
            "Create a directory tree inside the Agent Computer workspace.",
            _object({"computer_session_id": SESSION_ID, "path": PATH}, required=["computer_session_id", "path"]),
            tags=("filesystem", "write"),
        ),
        _capability(
            "computer.files.remove",
            "Remove sandbox path",
            "Delete a file or directory inside the Agent Computer workspace.",
            _object(
                {
                    "computer_session_id": SESSION_ID,
                    "path": PATH,
                    "recursive": {"type": "boolean"},
                },
                required=["computer_session_id", "path"],
            ),
            tags=("filesystem", "write"),
        ),
        _capability(
            "computer.files.move",
            "Move sandbox path",
            "Move or rename a file/directory inside the Agent Computer workspace.",
            _object(
                {"computer_session_id": SESSION_ID, "source": PATH, "destination": PATH},
                required=["computer_session_id", "source", "destination"],
            ),
            tags=("filesystem", "write"),
        ),
        _capability(
            "computer.files.search",
            "Search sandbox files",
            "Search filenames and text content in the Agent Computer workspace.",
            _object(
                {
                    "computer_session_id": SESSION_ID,
                    "query": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "path": {"type": "string", "maxLength": 2000},
                    "glob": {"type": "string", "maxLength": 500},
                    "max_matches": {"type": "integer", "minimum": 1, "maximum": 1000},
                },
                required=["computer_session_id", "query"],
            ),
            risk=CapabilityRisk.READ_ONLY,
            tags=("filesystem", "search", "code"),
        ),
        _capability(
            "computer.process.list",
            "List sandbox processes",
            "List long-running/background processes owned by the Agent Computer session.",
            session_only,
            risk=CapabilityRisk.READ_ONLY,
            tags=("process", "runtime"),
        ),
        _capability(
            "computer.process.kill",
            "Stop sandbox process",
            "Terminate a background process owned by the Agent Computer session.",
            _object(
                {
                    "computer_session_id": SESSION_ID,
                    "process_id": {"type": "string", "minLength": 1, "maxLength": 120},
                    "signal": {"type": "string", "enum": ["TERM", "KILL", "INT"]},
                },
                required=["computer_session_id", "process_id"],
            ),
            tags=("process", "runtime"),
        ),
        _capability(
            "computer.git.status",
            "Git status",
            "Read repository status inside the Agent Computer workspace.",
            _object(
                {"computer_session_id": SESSION_ID, "cwd": {"type": "string", "maxLength": 2000}},
                required=["computer_session_id"],
            ),
            risk=CapabilityRisk.READ_ONLY,
            tags=("git", "code"),
        ),
        _capability(
            "computer.git.diff",
            "Git diff",
            "Read a bounded git diff inside the Agent Computer workspace.",
            _object(
                {
                    "computer_session_id": SESSION_ID,
                    "cwd": {"type": "string", "maxLength": 2000},
                    "staged": {"type": "boolean"},
                    "path": {"type": "string", "maxLength": 2000},
                },
                required=["computer_session_id"],
            ),
            risk=CapabilityRisk.READ_ONLY,
            tags=("git", "code"),
        ),
        _capability(
            "computer.git.exec",
            "Run Git command",
            "Run an allowlisted git subcommand in the Agent Computer workspace.",
            _object(
                {
                    "computer_session_id": SESSION_ID,
                    "args": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 1000},
                        "minItems": 1,
                        "maxItems": 50,
                    },
                    "cwd": {"type": "string", "maxLength": 2000},
                    "timeout_seconds": TIMEOUT,
                },
                required=["computer_session_id", "args"],
            ),
            tags=("git", "code", "write"),
        ),
        _capability(
            "computer.web.fetch",
            "Fetch web resource",
            "Fetch a public HTTP(S) resource through the sandbox network policy.",
            _object(
                {
                    "computer_session_id": SESSION_ID,
                    "url": {"type": "string", "minLength": 8, "maxLength": 4096},
                    "method": {"type": "string", "enum": ["GET", "HEAD"]},
                    "max_bytes": {"type": "integer", "minimum": 1, "maximum": 5000000},
                },
                required=["computer_session_id", "url"],
            ),
            risk=CapabilityRisk.READ_ONLY,
            tags=("web", "network", "research"),
        ),
        _capability(
            "computer.web.download",
            "Download web resource",
            "Download a public HTTP(S) resource into the sandbox filesystem.",
            _object(
                {
                    "computer_session_id": SESSION_ID,
                    "url": {"type": "string", "minLength": 8, "maxLength": 4096},
                    "destination": PATH,
                    "max_bytes": {"type": "integer", "minimum": 1, "maximum": 50000000},
                },
                required=["computer_session_id", "url", "destination"],
            ),
            tags=("web", "network", "download"),
        ),
        _capability(
            "computer.browser.open",
            "Open browser",
            "Start the sandbox browser context for an Agent Computer session.",
            _object(
                {
                    "computer_session_id": SESSION_ID,
                    "viewport_width": {"type": "integer", "minimum": 320, "maximum": 2560},
                    "viewport_height": {"type": "integer", "minimum": 320, "maximum": 2000},
                },
                required=["computer_session_id"],
            ),
            tags=("browser", "web"),
        ),
        _capability(
            "computer.browser.navigate",
            "Navigate browser",
            "Navigate the sandbox browser to an HTTP(S) URL and return page metadata.",
            _object(
                {
                    "computer_session_id": SESSION_ID,
                    "url": {"type": "string", "minLength": 8, "maxLength": 4096},
                    "wait_until": {"type": "string", "enum": ["commit", "domcontentloaded", "load", "networkidle"]},
                    "timeout_seconds": TIMEOUT,
                },
                required=["computer_session_id", "url"],
            ),
            tags=("browser", "web"),
        ),
        _capability(
            "computer.browser.snapshot",
            "Read browser snapshot",
            "Return a compact accessibility/text snapshot of the current browser page.",
            _object(
                {
                    "computer_session_id": SESSION_ID,
                    "max_chars": {"type": "integer", "minimum": 1000, "maximum": 100000},
                },
                required=["computer_session_id"],
            ),
            risk=CapabilityRisk.READ_ONLY,
            tags=("browser", "web", "read"),
        ),
        _capability(
            "computer.browser.click",
            "Click browser element",
            "Click an element selected by role/text/CSS locator in the sandbox browser.",
            _object(
                {
                    "computer_session_id": SESSION_ID,
                    "selector": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "timeout_seconds": TIMEOUT,
                },
                required=["computer_session_id", "selector"],
            ),
            risk=CapabilityRisk.MEDIUM,
            tags=("browser", "interaction"),
        ),
        _capability(
            "computer.browser.type",
            "Type in browser",
            "Fill or type text into a selected browser element.",
            _object(
                {
                    "computer_session_id": SESSION_ID,
                    "selector": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "text": {"type": "string", "maxLength": 20000},
                    "press_enter": {"type": "boolean"},
                    "timeout_seconds": TIMEOUT,
                },
                required=["computer_session_id", "selector", "text"],
            ),
            risk=CapabilityRisk.MEDIUM,
            tags=("browser", "interaction"),
        ),
        _capability(
            "computer.browser.press",
            "Press browser key",
            "Send a keyboard key to the current browser page or selected element.",
            _object(
                {
                    "computer_session_id": SESSION_ID,
                    "key": {"type": "string", "minLength": 1, "maxLength": 80},
                    "selector": {"type": "string", "maxLength": 2000},
                },
                required=["computer_session_id", "key"],
            ),
            risk=CapabilityRisk.MEDIUM,
            tags=("browser", "interaction"),
        ),
        _capability(
            "computer.browser.evaluate",
            "Evaluate page JavaScript",
            "Evaluate bounded JavaScript in the current sandbox browser page context.",
            _object(
                {
                    "computer_session_id": SESSION_ID,
                    "expression": {"type": "string", "minLength": 1, "maxLength": 20000},
                },
                required=["computer_session_id", "expression"],
            ),
            risk=CapabilityRisk.MEDIUM,
            tags=("browser", "interaction", "javascript"),
        ),
        _capability(
            "computer.browser.screenshot",
            "Capture browser screenshot",
            "Capture the current browser page to a PNG file inside the sandbox.",
            _object(
                {
                    "computer_session_id": SESSION_ID,
                    "path": {"type": "string", "maxLength": 2000},
                    "full_page": {"type": "boolean"},
                },
                required=["computer_session_id"],
            ),
            risk=CapabilityRisk.READ_ONLY,
            tags=("browser", "screenshot", "artifact"),
        ),
        _capability(
            "computer.browser.close",
            "Close browser",
            "Close the sandbox browser context while keeping the Computer runtime alive.",
            session_only,
            tags=("browser", "lifecycle"),
        ),
    )


RUNNER_TOOL_BY_CAPABILITY = {
    spec.id: spec.id.removeprefix("computer.")
    for spec in computer_native_capabilities()
    if not spec.id.startswith("computer.runtime.")
}


class AgentComputerProvider:
    def __init__(self, runner: ComputerRunnerClient | None = None) -> None:
        self.runner = runner or ComputerRunnerClient()

    async def is_available(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
    ) -> bool:
        del db, context, capability
        return self.runner.configured

    async def _session(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        computer_session_id: str,
    ) -> AgentComputerSessionRecord:
        if not context.workspace_id or not context.user_id:
            raise PermissionError("Agent Computer requires a Workspace user context")
        row = await db.scalar(
            select(AgentComputerSessionRecord).where(
                AgentComputerSessionRecord.id == computer_session_id,
                AgentComputerSessionRecord.tenant_id == context.workspace_id,
                AgentComputerSessionRecord.user_id == context.user_id,
            )
        )
        if row is None:
            raise LookupError("Agent Computer session is unavailable in this Workspace/user scope")
        return row

    async def _record(
        self,
        db: AsyncSession,
        row: AgentComputerSessionRecord,
        *,
        capability_id: str,
        status: str,
        summary: str,
        payload: dict[str, Any],
    ) -> None:
        sequence = int(
            await db.scalar(
                select(func.coalesce(func.max(AgentComputerStepRecord.sequence), 0)).where(
                    AgentComputerStepRecord.session_id == row.id
                )
            )
            or 0
        ) + 1
        db.add(
            AgentComputerStepRecord(
                tenant_id=row.tenant_id,
                session_id=row.id,
                sequence=sequence,
                kind="computer_tool",
                status=status,
                capability_id=capability_id,
                summary=summary[:2000],
                payload_json=json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str),
            )
        )
        await db.flush()

    async def execute(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
        arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        del minimum_context
        row = await self._session(db, context, str(arguments["computer_session_id"]))
        try:
            if capability.id == "computer.runtime.start":
                result = await self._start(row, context, arguments)
            elif capability.id == "computer.runtime.status":
                result = await self._status(row)
            elif capability.id == "computer.runtime.stop":
                result = await self._stop(row)
            else:
                result = await self._tool(row, capability.id, arguments)
        except ComputerRunnerError as error:
            row.runtime_state = "unavailable"
            row.runtime_updated_at = datetime.utcnow()
            await self._record(
                db,
                row,
                capability_id=capability.id,
                status="failed",
                summary=str(error),
                payload={},
            )
            raise RuntimeError(str(error)) from error

        row.runtime_updated_at = datetime.utcnow()
        await self._record(
            db,
            row,
            capability_id=capability.id,
            status="completed",
            summary=f"{capability.display_name} completed inside the isolated Computer runtime.",
            payload={"result": result},
        )
        return CapabilityExecutionResult(
            value=result,
            resource_type="agent_computer_session",
            resource_id=row.id,
            event_payload={
                "computer_session_id": row.id,
                "runtime_session_id": row.runtime_session_id,
                "native_tool": capability.id,
            },
        )

    async def _start(
        self,
        row: AgentComputerSessionRecord,
        context: ExecutionContext,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if row.runtime_session_id and row.runtime_state in {"active", "starting"}:
            return await self.runner.status(row.runtime_session_id)
        profile = str(arguments.get("profile") or row.runtime_profile or "general")
        network_policy = str(arguments.get("network_policy") or row.network_policy or "web")
        ttl_seconds = max(60, min(int(arguments.get("ttl_seconds") or 7200), 21600))
        result = await self.runner.start(
            computer_session_id=row.id,
            workspace_id=str(context.workspace_id),
            principal_id=context.principal_id,
            profile=profile,
            ttl_seconds=ttl_seconds,
            network_policy=network_policy,
        )
        runtime_id = str(result.get("session_id") or result.get("id") or "").strip()
        if not runtime_id:
            raise ComputerRunnerError("Agent Computer runner did not return a session handle")
        row.runtime_session_id = runtime_id
        row.runtime_state = str(result.get("state") or "active")
        row.runtime_profile = profile
        row.network_policy = network_policy
        row.runtime_started_at = datetime.utcnow()
        return {
            **result,
            "computer_session_id": row.id,
            "runtime_session_id": runtime_id,
            "profile": profile,
            "network_policy": network_policy,
        }

    async def _status(self, row: AgentComputerSessionRecord) -> dict[str, Any]:
        if not row.runtime_session_id:
            return {
                "computer_session_id": row.id,
                "runtime_session_id": None,
                "state": "stopped",
                "profile": row.runtime_profile,
                "network_policy": row.network_policy,
            }
        result = await self.runner.status(row.runtime_session_id)
        row.runtime_state = str(result.get("state") or row.runtime_state or "unknown")
        return {**result, "computer_session_id": row.id, "runtime_session_id": row.runtime_session_id}

    async def _stop(self, row: AgentComputerSessionRecord) -> dict[str, Any]:
        if not row.runtime_session_id:
            row.runtime_state = "stopped"
            return {"computer_session_id": row.id, "state": "stopped", "already_stopped": True}
        runtime_id = row.runtime_session_id
        result = await self.runner.stop(runtime_id)
        row.runtime_state = "stopped"
        row.runtime_session_id = None
        return {**result, "computer_session_id": row.id, "runtime_session_id": runtime_id}

    async def _tool(
        self,
        row: AgentComputerSessionRecord,
        capability_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not row.runtime_session_id or row.runtime_state not in {"active", "ready", "running"}:
            raise ComputerRunnerError("Agent Computer runtime is not active; start computer.runtime.start first")
        runner_tool = RUNNER_TOOL_BY_CAPABILITY.get(capability_id)
        if not runner_tool:
            raise LookupError(f"Unknown Agent Computer native tool: {capability_id}")
        payload = {key: value for key, value in arguments.items() if key != "computer_session_id"}
        timeout = payload.get("timeout_seconds")
        result = await self.runner.tool(
            row.runtime_session_id,
            runner_tool,
            payload,
            timeout_seconds=float(timeout) + 10 if timeout else None,
        )
        row.runtime_state = str(result.get("runtime_state") or "active")
        return {
            **result,
            "computer_session_id": row.id,
            "runtime_session_id": row.runtime_session_id,
            "tool": capability_id,
        }
