from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.principal_models import WorkspaceToolExposure
from packages.kernel.contracts import CapabilityRisk, CapabilitySpec, RuntimeRequest
from packages.kernel.ingress import TrustedIngress, resolve_ingress_context
from packages.kernel.runtime import RuntimeExecutionError
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind
from packages.workspace_modules.tools.runtime import build_workspace_runtime


@dataclass(frozen=True, slots=True)
class McpRequestContext:
    """Trusted identity facts for one MCP caller.

    ``token_scopes`` are a narrowing policy only. Workspace membership, permissions,
    provider availability and approvals are re-resolved by Operly for every request.
    """

    tenant_id: str
    user_id: str
    client_id: str
    token_scopes: frozenset[str]
    grant_id: str | None = None
    objective: str = "Operly MCP request"
    conversation_id: str | None = None
    enforce_exposure: bool = True


class McpGatewayError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _clean_scopes(scopes: Iterable[str]) -> frozenset[str]:
    return frozenset(str(item or "").strip().lower() for item in scopes if str(item or "").strip())


def scope_rule_covers(granted_rule: str, requested_rule: str) -> bool:
    """Return whether one grant rule covers another narrowing rule."""

    granted = str(granted_rule or "").strip().lower()
    requested = str(requested_rule or "").strip().lower()
    if not granted or not requested:
        return False
    if granted == "workspace:*" or granted == requested:
        return True
    if granted.endswith(".*"):
        prefix = granted[:-1]
        return requested.startswith(prefix) and ":" not in requested
    return False


def narrow_scope_rules(requested: Iterable[str], granted: Iterable[str]) -> frozenset[str]:
    grant_rules = _clean_scopes(granted)
    return frozenset(
        rule
        for rule in _clean_scopes(requested)
        if any(scope_rule_covers(grant, rule) for grant in grant_rules)
    )


def scope_allows(spec: CapabilitySpec, scopes: Iterable[str]) -> bool:
    """Apply MCP client scope as a post-authorization visibility restriction."""

    rules = _clean_scopes(scopes)
    if not rules:
        return False
    if "workspace:*" in rules or spec.id.lower() in rules:
        return True
    capability = spec.id.lower()
    for rule in rules:
        if rule.endswith(".*") and capability.startswith(rule[:-1]):
            return True
    permissions = {permission.lower() for permission in spec.permissions}
    return bool(permissions.intersection(rules))


def _destructive_hint(spec: CapabilitySpec) -> bool:
    lowered = spec.id.lower()
    destructive_words = (
        ".delete",
        ".remove",
        ".archive",
        ".rollback",
        ".cancel",
        ".kill",
        ".stop",
        ".disconnect",
        ".revoke",
    )
    return spec.risk is CapabilityRisk.HIGH or any(word in lowered for word in destructive_words)


def _open_world_hint(spec: CapabilitySpec) -> bool:
    tags = {item.lower() for item in spec.tags}
    return bool(
        tags.intersection({"web", "browser", "gmail", "calendar", "discord", "canva", "integration", "external"})
        or spec.id.startswith(("computer.web.", "computer.browser.", "gmail.", "calendar.", "discord.", "canva."))
    )


def agent_description(spec: CapabilitySpec) -> str:
    """Generate concise operational guidance intended for an AI tool selector."""

    parts: list[str] = []
    base = str(spec.description or "").strip()
    if base:
        parts.append(base)
    else:
        parts.append(f"Use {spec.display_name} for this Workspace operation.")

    if spec.risk is CapabilityRisk.READ_ONLY:
        parts.append("This tool is read-only and should be preferred when you only need to inspect or discover state.")
    elif spec.risk is CapabilityRisk.HIGH:
        parts.append("This is a high-impact Workspace action; verify the target and arguments before calling it.")

    if spec.approval_required:
        parts.append(
            "This invocation may pause at Operly's human approval boundary. If approval is required, do not retry blindly: preserve the returned request ID, run ID and approval ID, wait for the human decision, then resume the exact invocation."
        )

    if spec.id == "computer.runtime.start":
        parts.append(
            "Use this to allocate the isolated Agent Computer runtime before native Computer work. The returned runtime belongs to the supplied Operly Computer session."
        )
    elif spec.id.startswith("computer."):
        parts.append(
            "This is a native Agent Computer tool. It requires computer_session_id for an active Computer session owned by the same Workspace user; start the runtime first when necessary."
        )

    if spec.id.startswith("workflow."):
        parts.append(
            "Workflow tools manage durable, traceable orchestration. Use them when work must persist, wait, branch, retry safely, or run on a schedule instead of keeping the agent process alive."
        )

    if spec.reversible:
        parts.append("Operly marks this capability as reversible, but reversal is still a separate governed action where applicable.")

    return " ".join(parts)[:1800]


def tool_definition(spec: CapabilitySpec) -> dict[str, Any]:
    """Translate one canonical CapabilitySpec into an MCP tool contract."""

    return {
        "name": spec.id,
        "title": spec.display_name,
        "description": agent_description(spec),
        "inputSchema": dict(spec.input_schema),
        "outputSchema": dict(spec.output_schema),
        "annotations": {
            "readOnlyHint": spec.risk is CapabilityRisk.READ_ONLY,
            "destructiveHint": _destructive_hint(spec),
            "idempotentHint": spec.risk is CapabilityRisk.READ_ONLY,
            "openWorldHint": _open_world_hint(spec),
        },
        "_meta": {
            "operly/capabilityId": spec.id,
            "operly/providerId": spec.provider_id,
            "operly/risk": spec.risk.value,
            "operly/approvalRequired": spec.approval_required,
            "operly/reversible": spec.reversible,
            "operly/permissions": list(spec.permissions),
            "operly/tags": sorted(spec.tags),
        },
    }


class McpGateway:
    """MCP projection of the canonical governed Workspace runtime."""

    def __init__(self, runtime=None) -> None:
        self._runtime = runtime if runtime is not None else build_workspace_runtime()

    async def execution_context(self, db: AsyncSession, request: McpRequestContext) -> ExecutionContext:
        return await resolve_ingress_context(
            db,
            TrustedIngress(
                scope_kind=ScopeKind.WORKSPACE,
                user_id=request.user_id,
                workspace_id=request.tenant_id,
                channel="mcp",
                surface=SurfaceKind.MCP_CLIENT,
                conversation_id=request.conversation_id,
                metadata={
                    "ingress": "operly_mcp",
                    "mcp_client_id": request.client_id,
                    "mcp_grant_id": request.grant_id,
                },
            ),
        )

    async def _exposure_map(self, db: AsyncSession, request: McpRequestContext) -> dict[str, bool]:
        if not request.enforce_exposure:
            return {}
        rows = (
            await db.scalars(
                select(WorkspaceToolExposure).where(
                    WorkspaceToolExposure.tenant_id == request.tenant_id,
                    WorkspaceToolExposure.surface == "mcp",
                )
            )
        ).all()
        return {row.tool_id.lower(): bool(row.exposed) for row in rows}

    async def available_specs(
        self,
        db: AsyncSession,
        request: McpRequestContext,
        *,
        query: str | None = None,
    ) -> tuple[CapabilitySpec, ...]:
        context = await self.execution_context(db, request)
        specs = await self._runtime.available_capabilities(
            db,
            context=context,
            query=query,
            limit=1000 if not query else 100,
        )
        exposure = await self._exposure_map(db, request)
        allowed = [
            spec
            for spec in specs
            if spec.resource_scope == "workspace"
            and scope_allows(spec, request.token_scopes)
            and exposure.get(spec.id.lower(), True)
        ]
        return tuple(sorted(allowed, key=lambda item: item.id))

    async def list_tools(self, db: AsyncSession, request: McpRequestContext) -> list[dict[str, Any]]:
        return [tool_definition(spec) for spec in await self.available_specs(db, request)]

    async def _available_spec(
        self,
        db: AsyncSession,
        request: McpRequestContext,
        tool_id: str,
    ) -> tuple[ExecutionContext, CapabilitySpec]:
        normalized = str(tool_id or "").strip().lower()
        if not normalized:
            raise McpGatewayError("tool_not_found", "MCP tool name is required")
        context = await self.execution_context(db, request)
        specs = await self._runtime.available_capabilities(db, context=context, query=normalized, limit=100)
        exposure = await self._exposure_map(db, request)
        for spec in specs:
            if (
                spec.id == normalized
                and spec.resource_scope == "workspace"
                and scope_allows(spec, request.token_scopes)
                and exposure.get(spec.id.lower(), True)
            ):
                return context, spec
        raise McpGatewayError(
            "tool_unavailable",
            "This MCP tool is hidden, outside the client grant, unavailable from its provider, or no longer authorized for the current Workspace principal.",
        )

    async def call_tool(
        self,
        db: AsyncSession,
        request: McpRequestContext,
        *,
        tool_id: str,
        arguments: dict[str, Any],
        goal: str | None = None,
        request_id: str | None = None,
        approval_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise McpGatewayError("invalid_arguments", "Tool arguments must be a JSON object")
        context, spec = await self._available_spec(db, request, tool_id)
        stable_request_id = request_id or f"mcp:{request.client_id}:{uuid4()}"
        try:
            response = await self._runtime.execute(
                db,
                context=context,
                request=RuntimeRequest(
                    goal=(goal or request.objective or f"MCP call to {spec.id}")[:4000],
                    capability_id=spec.id,
                    arguments=arguments,
                    conversation_id=conversation_id or request.conversation_id,
                    request_id=stable_request_id,
                    approval_id=approval_id,
                ),
            )
        except RuntimeExecutionError as error:
            waiting = error.code == "approval_required" and bool(error.approval_id)
            return {
                "ok": False,
                "status": "waiting_for_approval" if waiting else "failed",
                "capability_id": spec.id,
                "request_id": stable_request_id,
                "run_id": error.run_id,
                "approval_id": error.approval_id,
                "error": {"code": error.code, "message": str(error)},
                "agent_instruction": (
                    "Human approval is required. Do not repeat the action. Preserve request_id, run_id and approval_id; after the human decides, resume the exact same tool call with the same request ID and the returned approval ID."
                    if waiting
                    else "The governed capability failed. Inspect the error and current Workspace/provider state before deciding whether a new invocation is safe."
                ),
            }
        return {
            "ok": True,
            "status": response.status,
            "capability_id": response.capability_id or spec.id,
            "request_id": stable_request_id,
            "run_id": response.run_id,
            "result": dict(response.result or {}),
            "done": response.done,
            "decision": response.decision.value,
            "trace": [dict(item) for item in response.trace],
        }
