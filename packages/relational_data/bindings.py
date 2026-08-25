"""Resolve semantic data bindings into runner-only scoped grants.

Transport grants are attached to a copy of ``BuildSubmission`` only after the durable
build record has stored the credential-free semantic submission. This remains the
single build-service entrypoint for built-in data-plane bindings.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from packages.runtime_plugins.runner_contracts import ServiceBindingTransport
from packages.relational_data.contracts import RELATIONAL_CAPABILITY_ID
from packages.relational_data.store import configured_app_data_url
from packages.relational_data.tokens import BindingGrantError, issue_binding_grant


class RelationalBindingUnavailable(RuntimeError):
    pass


def relational_binding_requests(submission) -> list:
    return [item for item in submission.serviceBindings if item.capabilityId == RELATIONAL_CAPABILITY_ID]


def _gateway_url() -> str:
    configured = (
        os.getenv("OPERLY_RELATIONAL_GATEWAY_URL", "").strip().rstrip("/")
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
        raise RelationalBindingUnavailable("Relational capability gateway URL is not configured")
    if environment in {"production", "prod"} and parsed.scheme != "https":
        raise RelationalBindingUnavailable("Relational capability gateway must use HTTPS in production")
    return configured


def attach_transport_grants(submission):
    result = submission
    requests = relational_binding_requests(result)
    if requests:
        try:
            configured_app_data_url()
            gateway = _gateway_url()
            ttl = max(900, int(result.resources.previewSeconds) + 900)
            bindings = []
            for request in result.serviceBindings:
                if request.capabilityId != RELATIONAL_CAPABILITY_ID:
                    bindings.append(request)
                    continue
                bindings.append(
                    request.model_copy(
                        update={
                            "transport": ServiceBindingTransport(
                                gatewayUrl=gateway,
                                runtimeToken=issue_binding_grant(
                                    result.workspaceId,
                                    result.applicationId,
                                    scopes=("read", "write"),
                                    ttl_seconds=ttl,
                                ),
                                migrationToken=issue_binding_grant(
                                    result.workspaceId,
                                    result.applicationId,
                                    scopes=("migrate",),
                                    ttl_seconds=900,
                                ),
                            )
                        }
                    )
                )
            result = result.model_copy(update={"serviceBindings": bindings})
        except (BindingGrantError, ValueError) as error:
            raise RelationalBindingUnavailable(str(error)) from error

    # Keep one build-service transport entrypoint while each semantic capability owns
    # its own grant rules. Imports stay lazy so runner contract modules stay light.
    try:
        from packages.workspace_entities.bindings import (
            WorkspaceEntityBindingUnavailable,
            attach_workspace_entity_grants,
        )
        result = attach_workspace_entity_grants(result)
    except WorkspaceEntityBindingUnavailable as error:
        raise RelationalBindingUnavailable(str(error)) from error

    try:
        from packages.app_identity.bindings import (
            AppIdentityBindingUnavailable,
            attach_app_identity_grants,
        )
        result = attach_app_identity_grants(result)
    except AppIdentityBindingUnavailable as error:
        raise RelationalBindingUnavailable(str(error)) from error
    return result


__all__ = ["RelationalBindingUnavailable", "relational_binding_requests", "attach_transport_grants"]
