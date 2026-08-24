"""Resolve workspace entity semantic bindings into runner-only scoped grants."""
from __future__ import annotations

import os
from urllib.parse import urlparse

from packages.custom_software.runner_contracts import ServiceBindingTransport
from packages.relational_data.store import configured_app_data_url
from packages.relational_data.tokens import BindingGrantError, issue_capability_grant
from packages.workspace_entities.contracts import CANONICAL_ENTITY_SCHEMAS, WORKSPACE_ENTITY_CAPABILITY_ID


class WorkspaceEntityBindingUnavailable(RuntimeError):
    pass


def workspace_entity_binding_requests(submission) -> list:
    return [item for item in submission.serviceBindings if item.capabilityId == WORKSPACE_ENTITY_CAPABILITY_ID]


def _gateway_url() -> str:
    configured = (
        os.getenv("OPERLY_ENTITY_GATEWAY_URL", "").strip().rstrip("/")
        or os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    )
    environment = os.getenv("OPERLY_ENV", os.getenv("APP_ENV", "development")).lower()
    if not configured and environment not in {"production", "prod"}:
        configured = "http://host.docker.internal:8000"
    parsed = urlparse(configured)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise WorkspaceEntityBindingUnavailable("Workspace entity gateway URL is not configured")
    if environment in {"production", "prod"} and parsed.scheme != "https":
        raise WorkspaceEntityBindingUnavailable("Workspace entity gateway must use HTTPS in production")
    return configured


def attach_workspace_entity_grants(submission):
    requests = workspace_entity_binding_requests(submission)
    if not requests:
        return submission
    seen: set[str] = set()
    for request in requests:
        kind = request.semanticName
        if kind not in CANONICAL_ENTITY_SCHEMAS:
            raise WorkspaceEntityBindingUnavailable(f"Unknown canonical entity binding: {kind}")
        if kind in seen:
            raise WorkspaceEntityBindingUnavailable(f"Duplicate canonical entity binding: {kind}")
        seen.add(kind)
    try:
        configured_app_data_url()
        gateway = _gateway_url()
        ttl = max(900, int(submission.resources.previewSeconds) + 900)
        bindings = []
        for request in submission.serviceBindings:
            if request.capabilityId != WORKSPACE_ENTITY_CAPABILITY_ID:
                bindings.append(request)
                continue
            kind = request.semanticName
            token = issue_capability_grant(
                submission.workspaceId,
                submission.applicationId,
                capability_id=WORKSPACE_ENTITY_CAPABILITY_ID,
                scopes=("read", "write"),
                allowed_scopes=frozenset({"read", "write"}),
                resources=(kind,),
                ttl_seconds=ttl,
            )
            bindings.append(
                request.model_copy(update={"transport": ServiceBindingTransport(gatewayUrl=gateway, runtimeToken=token)})
            )
        return submission.model_copy(update={"serviceBindings": bindings})
    except (BindingGrantError, ValueError) as error:
        raise WorkspaceEntityBindingUnavailable(str(error)) from error


__all__ = [
    "WorkspaceEntityBindingUnavailable",
    "workspace_entity_binding_requests",
    "attach_workspace_entity_grants",
]
