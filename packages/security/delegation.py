"""Fail-closed delegated execution principals for the shared capability fabric.

Delegation is application-created execution state, never model-authored capability
arguments or request metadata. It narrows an already-authorized workspace execution
context to an exact capability allowlist for a workflow, software runtime, or child
agent. The underlying human remains the accountable delegator for approval and audit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from packages.security.execution_context import ExecutionContext, ScopeKind


_ALLOWED_PRINCIPAL_KINDS = frozenset(
    {
        "workflow",
        "software_project",
        "production",
        "agent_run",
        "app_user",
        "public_session",
    }
)
_CAPABILITY_ID = re.compile(r"^[a-z][a-z0-9_.:-]{1,159}$")
_PRINCIPAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,95}$")
_DELEGATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,119}$")
_MAX_CAPABILITIES = 128


@dataclass(frozen=True, slots=True)
class DelegatedExecutionContext(ExecutionContext):
    """An ExecutionContext narrowed to one non-human principal.

    Permissions, workspace membership, surface and authenticated ``user_id`` are
    inherited from the trusted base context. ``delegated_capability_ids`` is an
    additional mandatory allowlist enforced by CapabilityFirewall; it can never widen
    the base human authority.
    """

    principal_kind: str = "software_project"
    principal_id: str = ""
    delegation_id: str = ""
    delegated_capability_ids: frozenset[str] = frozenset()
    parent_principal_id: str = ""

    @property
    def principal_key(self) -> str:
        return f"{self.principal_kind}:{self.principal_id}"


def effective_principal_key(context: ExecutionContext) -> str | None:
    if isinstance(context, DelegatedExecutionContext):
        return context.principal_key
    return f"user:{context.user_id}" if context.user_id else None


def delegation_id(context: ExecutionContext) -> str | None:
    if isinstance(context, DelegatedExecutionContext):
        return context.delegation_id
    return None


def delegation_allows(context: ExecutionContext, capability_id: str) -> bool:
    if not isinstance(context, DelegatedExecutionContext):
        return True
    return capability_id in context.delegated_capability_ids


def delegation_authority(context: ExecutionContext) -> dict[str, object]:
    if isinstance(context, DelegatedExecutionContext):
        return {
            "principal_kind": context.principal_kind,
            "principal_id": context.principal_key,
            "delegation_id": context.delegation_id,
            "delegator_user_id": context.user_id,
            "parent_principal_id": context.parent_principal_id,
            "delegated_capability_ids": sorted(context.delegated_capability_ids),
        }
    return {
        "principal_kind": "user" if context.user_id else "anonymous",
        "principal_id": effective_principal_key(context),
        "delegation_id": None,
        "delegator_user_id": None,
        "parent_principal_id": None,
        "delegated_capability_ids": None,
    }


def delegate_execution_context(
    base: ExecutionContext,
    *,
    principal_kind: str,
    principal_id: str,
    capability_ids: set[str] | frozenset[str],
    delegation_id_value: str,
) -> DelegatedExecutionContext:
    """Create a strictly narrower workspace execution context.

    This helper is for trusted Operly runtime code only. It intentionally refuses
    personal scopes for the first delegation version so a generated/workflow principal
    cannot accidentally acquire account-wide private connector authority.
    """

    if base.scope_kind is not ScopeKind.WORKSPACE or not base.workspace_id:
        raise PermissionError("Delegated principals currently require workspace authority")
    if not base.user_id or not base.membership_id:
        raise PermissionError("Delegation requires a currently authenticated workspace member")

    kind = str(principal_kind or "").strip().lower()
    if kind not in _ALLOWED_PRINCIPAL_KINDS:
        raise ValueError("Unsupported delegated principal kind")

    clean_principal = str(principal_id or "").strip()
    if not _PRINCIPAL_ID.fullmatch(clean_principal):
        raise ValueError("Delegated principal id is invalid")

    clean_delegation = str(delegation_id_value or "").strip()
    if not _DELEGATION_ID.fullmatch(clean_delegation):
        raise ValueError("Delegation id is invalid")

    requested = frozenset(str(item or "").strip() for item in capability_ids)
    if not requested or len(requested) > _MAX_CAPABILITIES:
        raise ValueError("Delegation requires a bounded non-empty capability allowlist")
    if any(not _CAPABILITY_ID.fullmatch(item) for item in requested):
        raise ValueError("Delegation contains an invalid capability id")

    if isinstance(base, DelegatedExecutionContext):
        if not requested.issubset(base.delegated_capability_ids):
            raise PermissionError("Child delegation cannot widen parent capability authority")
        parent_principal = base.principal_key
    else:
        parent_principal = f"user:{base.user_id}"

    return DelegatedExecutionContext(
        workspace_id=base.workspace_id,
        user_id=base.user_id,
        membership_id=base.membership_id,
        role=base.role,
        permissions=base.permissions,
        channel=base.channel,
        surface=base.surface,
        conversation_id=base.conversation_id,
        metadata=dict(base.metadata),
        scope_kind=base.scope_kind,
        focus_workspace_id=base.focus_workspace_id,
        principal_kind=kind,
        principal_id=clean_principal,
        delegation_id=clean_delegation,
        delegated_capability_ids=requested,
        parent_principal_id=parent_principal,
    )


__all__ = [
    "DelegatedExecutionContext",
    "delegate_execution_context",
    "delegation_allows",
    "delegation_authority",
    "delegation_id",
    "effective_principal_key",
]
