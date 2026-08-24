"""Resolve workspace entity declarations into runner-only scoped grants."""
from __future__ import annotations

import os
from urllib.parse import urlparse

from packages.custom_software.runner_contracts import ServiceBindingTransport
from packages.relational_data.store import configured_app_data_url
from packages.relational_data.tokens import BindingGrantError, issue_capability_grant
from packages.workspace_entities.contracts import WORKSPACE_ENTITY_CAPABILITY_ID
from packages.workspace_entities.manifest import parse_workspace_entity_manifest


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


def attach_workspace_entity_grants(submission, source_bundle):
    requests = workspace_entity_binding_requests(submission)
    if not requests:
        return submission
    if len(requests) != 1:
        raise WorkspaceEntityBindingUnavailable("Exactly one workspace entity graph binding is supported")
    declaration = parse_workspace_entity_manifest(source_bundle)
    if declaration is None:
        raise WorkspaceEntityBindingUnavailable("Workspace entity declaration is missing")
    try:
        configured_app_data_url()
        gateway = _gateway_url()
        scopes = tuple(sorted({scope for item in declaration.entities for scope in item.access}))
        resources = tuple(sorted({item.kind for item in declaration.entities}))
        ttl = max(900, int(submission.resources.previewSeconds) + 900)
        bindings = []
        for request in submission.serviceBindings:
            if request.capabilityId != WORKSPACE_ENTITY_CAPABILITY_ID:
                bindings.append(request)
                continue
            token = issue_capability_grant(
                submission.workspaceId,
                submission.applicationId,
                capability_id=WORKSPACE_ENTITY_CAPABILITY_ID,
                scopes=scopes,
                allowed_scopes=frozenset({"read", "write"}),
                resources=resources,
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
