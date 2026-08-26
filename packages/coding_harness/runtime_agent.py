"""Software-specialized adapter over the canonical Operly AgentRuntime.

Studio is a surface, not an agent runtime.  This module preserves the bounded virtual
source workspace and software-specific tools, while delegating the model/tool loop to
``packages.agents.runtime.AgentRuntime``.  Planning, retries and completion truth stay
outside the model; executable verification remains in the isolated runner.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
from typing import Any

from packages.agents.runtime import AgentExecutionBudget, AgentRuntime
from packages.coding_harness.model_client import coding_model_client
from packages.coding_harness.opencode_agent import (
    AgentMode,
    AgentTrace,
    BUILD_SYSTEM,
    PLAN_SYSTEM,
    CodingAgentNeedsUserInput,
    CodingHarnessError,
    CodingHarnessResult,
    CodingSession,
    CodingToolRegistry,
    CodingWebToolError,
    VirtualWorkspace,
    WorkspacePolicyError,
    _has_test,
    _objective_completion_audit,
    _progress_summary,
    _tool_signature,
)
from packages.coding_harness.runtime_resolution import RuntimeResolutionError, validate_source_files
from packages.custom_software.source_bundles import SourceFile
from packages.model_runtime.trace_context import current_trace_metadata


class AgentRuntimeCodingAgent:
    """Software worker whose only model loop is the canonical ``AgentRuntime``."""

    def __init__(
        self,
        client=None,
        max_steps: int | None = None,
        registry: CodingToolRegistry | None = None,
        progress_callback=None,
    ) -> None:
        self.client = client or coding_model_client()
        configured = int(os.getenv("OPERLY_CODING_AGENT_MAX_STEPS", "56"))
        self.max_steps = max(4, min(max_steps or configured, 120))
        configured_seconds = int(os.getenv("OPERLY_CODING_AGENT_MAX_SECONDS", "240"))
        self.max_seconds = max(30, min(configured_seconds, 900))
        self.registry = registry or CodingToolRegistry()
        self.doom_loop_threshold = 3
        self.progress_callback = progress_callback

    async def _progress(self, event: dict[str, Any]) -> None:
        if self.progress_callback is None:
            return
        value = self.progress_callback(event)
        if inspect.isawaitable(value):
            await value

    async def plan(
        self,
        specification: str,
        files: list[SourceFile] | None = None,
        task: str = "Plan the implementation.",
        *,
        context: dict[str, Any] | None = None,
    ) -> str:
        session = await self._session(
            "plan",
            specification,
            VirtualWorkspace(files),
            task,
            require_change=False,
            editor_context=context or {},
        )
        return session.summary

    async def build(
        self,
        specification: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> CodingHarnessResult:
        session = await self._session(
            "build",
            specification,
            VirtualWorkspace(),
            "Create the complete implementation.",
            require_change=False,
            editor_context=context or {},
        )
        return self._result(session)

    async def edit(
        self,
        specification: str,
        files: list[SourceFile],
        instruction: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> CodingHarnessResult:
        task = str(instruction or "").strip()
        if not task:
            raise CodingHarnessError("Source edit instruction is empty")
        session = await self._session(
            "edit",
            specification,
            VirtualWorkspace(files),
            task[:20_000],
            require_change=True,
            editor_context=context or {},
        )
        return self._result(session)

    async def repair(
        self,
        specification: str,
        files: list[SourceFile],
        failure_evidence: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> CodingHarnessResult:
        evidence = json.dumps(
            failure_evidence or {}, ensure_ascii=False, sort_keys=True
        )[:24_000]
        task = (
            "Repair the smallest amount of source necessary for the isolated runner to pass.\n"
            "RUNNER EVIDENCE:\n" + evidence
        )
        session = await self._session(
            "repair",
            specification,
            VirtualWorkspace(files),
            task,
            require_change=True,
            editor_context=context or {},
        )
        return self._result(session)

    def _result(self, session: CodingSession) -> CodingHarnessResult:
        model_client = getattr(self.client, "inner", self.client)
        plan = (
            session.notes[0][:20_000]
            if session.notes
            else "Canonical AgentRuntime executed against the approved software specification."
        )
        return CodingHarnessResult(
            files=session.workspace.source_files(),
            plan=plan,
            summary=session.summary or "Source tree authored.",
            verification=session.verification,
            trace=session.trace,
            changed_paths=session.changed_paths(),
            model_provider=(
                "ollama" if hasattr(model_client, "model") else type(model_client).__name__
            ),
            model_id=str(
                getattr(model_client, "last_model", getattr(model_client, "model", "unknown"))
            ),
        )

    @staticmethod
    def _can_implicit_finish(session: CodingSession, require_change: bool) -> bool:
        files = session.workspace.source_files()
        if not files or not _has_test(files):
            return False
        if require_change and not session.changed_paths():
            return False
        objective_audit = _objective_completion_audit(session)
        if objective_audit is not None and not bool(objective_audit.get("verified")):
            session.last_validation_error = (
                "Objective audit: "
                + str(objective_audit.get("message") or "approved objective is incomplete")
            )[:4000]
            return False
        try:
            validate_source_files(files)
        except RuntimeResolutionError:
            return False
        return True

    async def _session(
        self,
        mode: AgentMode,
        specification: str,
        workspace: VirtualWorkspace,
        task: str,
        *,
        require_change: bool,
        editor_context: dict[str, Any],
    ) -> CodingSession:
        spec = str(specification or "").strip()
        if not spec:
            raise CodingHarnessError("Approved specification is empty")
        spec = spec[:80_000]
        session = CodingSession(
            mode=mode,
            workspace=workspace,
            before=workspace.snapshot(),
            editor_context=editor_context,
            approved_specification=spec,
        )
        system = PLAN_SYSTEM if mode == "plan" else BUILD_SYSTEM
        files = workspace.list()
        task_text = str(task or "")[:24_000]
        user_packet: dict[str, Any] = {
            "approvedSpecification": spec,
            "task": task_text,
            "workspaceFiles": files,
            "mode": mode,
            "executionBoundary": (
                "Generated code never executes in the Operly control plane; deterministic "
                "runner evidence is supplied to later repair stages."
            ),
            "runtime": "canonical_agent_runtime",
        }
        if editor_context:
            user_packet["editorContextAvailable"] = True
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user_packet, ensure_ascii=False)},
        ]
        session.messages = messages

        web_enabled = bool(os.getenv("OLLAMA_API_KEY", "").strip()) and os.getenv(
            "OPERLY_CODING_WEB_TOOLS", "1"
        ).strip() not in {"0", "false", "False"}
        tools = self.registry.for_mode(mode, visual=bool(editor_context), web=web_enabled)
        call_signatures: list[str] = []
        call_index = 0

        await self._progress(
            {
                "step": 0,
                "phase": "model_input",
                "summary": (
                    f"Canonical AgentRuntime input prepared: {len(spec)} specification chars · "
                    f"{len(files)} workspace file{'s' if len(files) != 1 else ''} · "
                    f"{len(tools)} tool{'s' if len(tools) != 1 else ''}."
                ),
                "detail": {
                    "mode": mode,
                    "runtime": "AgentRuntime",
                    "workspaceFileCount": len(files),
                    "workspaceFiles": files[:50],
                    "editorContextAvailable": bool(editor_context),
                    "toolNames": list(tools),
                },
            }
        )

        async def schemas() -> list[dict[str, Any]]:
            return [tool.schema() for tool in tools.values()]

        async def invoke(
            name: str,
            arguments: dict[str, Any],
            call_id: str | None,
        ) -> dict[str, Any]:
            nonlocal call_index
            del call_id
            call_index += 1
            args = dict(arguments or {})
            signature = _tool_signature(name, args)
            call_signatures.append(signature)
            if (
                len(call_signatures) >= self.doom_loop_threshold
                and len(set(call_signatures[-self.doom_loop_threshold :])) == 1
            ):
                raise CodingHarnessError(
                    f"Software worker repeated the same {name or 'unknown'} tool call "
                    f"{self.doom_loop_threshold} times"
                )

            tool = tools.get(name)
            path = str(
                args.get("path") or args.get("prefix") or args.get("pattern") or ""
            ) or None
            if tool is None:
                result: dict[str, Any] = {
                    "ok": False,
                    "error": f"Tool {name or 'unknown'} is not permitted in {mode} mode",
                }
            else:
                try:
                    result = dict(await tool.execute(args, session))
                except CodingAgentNeedsUserInput:
                    raise
                except (
                    WorkspacePolicyError,
                    CodingWebToolError,
                    ValueError,
                    TypeError,
                ) as error:
                    result = {"ok": False, "error": str(error)[:2000]}

            session.trace.append(
                AgentTrace(
                    step=call_index,
                    tool=name or "unknown",
                    path=path,
                    ok=bool(result.get("ok", False)),
                    detail=str(result.get("error") or result.get("message") or "")[:500],
                    input_digest=signature[:16],
                )
            )
            await self._progress(
                {
                    "step": call_index,
                    "phase": "tool",
                    "tool": name or "unknown",
                    "path": path,
                    "ok": bool(result.get("ok", False)),
                    "summary": _progress_summary(
                        name or "unknown", path, bool(result.get("ok", False))
                    ),
                }
            )
            if session.finished:
                return {**result, "status": "VERIFIED", "verified": True}
            return result

        inherited = current_trace_metadata()
        runtime_metadata = {
            **dict(inherited or {}),
            "runtime_component": "software_agent_runtime",
            "software_agent_mode": mode,
            "worker_role": "coding_agent",
        }
        base_steps = min(8, self.max_steps)
        runtime = AgentRuntime(
            max_steps=base_steps,
            execution_budget=AgentExecutionBudget(
                base_steps=base_steps,
                max_steps=self.max_steps,
                extension_steps=4,
                max_tool_calls=max(64, min(self.max_steps * 4, 256)),
            ),
        )
        try:
            outcome = await asyncio.wait_for(
                runtime.run(
                    model=self.client,
                    messages=messages,
                    schemas=schemas,
                    invoke=invoke,
                    inference_metadata=runtime_metadata,
                ),
                timeout=self.max_seconds,
            )
        except asyncio.TimeoutError as error:
            detail = (
                f" Last validation issue: {session.last_validation_error}"
                if session.last_validation_error
                else ""
            )
            raise CodingHarnessError(
                f"Canonical software AgentRuntime did not converge within "
                f"{self.max_seconds} seconds.{detail}"
            ) from error

        session.messages = list(outcome.get("messages") or messages)
        for message in session.messages:
            if str(message.get("role") or "") != "assistant":
                continue
            content = str(message.get("content") or "").strip()
            if content and content not in session.notes:
                session.notes.append(content)

        if not session.finished and mode != "plan" and self._can_implicit_finish(
            session, require_change
        ):
            session.summary = str(outcome.get("message") or "Source tree authored.")[:4000]
            session.finished = True

        if not session.finished:
            detail = (
                f" Last validation issue: {session.last_validation_error}"
                if session.last_validation_error
                else ""
            )
            raise CodingHarnessError(
                "Canonical software AgentRuntime stopped before the software completion "
                f"contract was satisfied ({outcome.get('stop_reason') or 'incomplete'}).{detail}"
            )

        if mode != "plan":
            final_files = workspace.source_files()
            if not final_files:
                raise CodingHarnessError("Source tree is empty")
            if not _has_test(final_files):
                raise CodingHarnessError(
                    "Executable test file is required before source completion"
                )
            if require_change and not session.changed_paths():
                raise CodingHarnessError(
                    "The requested edit/repair did not change any source files"
                )
        return session


# Compatibility aliases keep service imports stable while the implementation is no
# longer a second coding-agent runtime.
CapabilityCodingAgent = AgentRuntimeCodingAgent
OpenCodeStyleCodingAgent = AgentRuntimeCodingAgent
