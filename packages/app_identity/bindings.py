"""Resolve generated-app identity bindings into runner-only scoped grants."""
from __future__ import annotations

import os
from urllib.parse import urlparse

from packages.app_identity.contracts import APP_IDENTITY_BINDING_NAME, APP_IDENTITY_CAPABILITY_ID
from packages.app_identity.crypto import identity_secret
from packages.custom_software.runner_contracts import ServiceBindingTransport
from packages.relational_data.store import configured_app_data_url
from packages.relational_data.tokens import BindingGrantError, issue_capability_grant


class AppIdentityBindingUnavailable(RuntimeError):
    pass


def app_identity_binding_requests(submission) -> list:
    return [item for item in submission.serviceBindings if item.capabilityId == APP_IDENTITY_CAPABILITY_ID]


def _gateway_url() -> str:
    configured = (
        os.getenv("OPERLY_APP_IDENTITY_GATEWAY_URL", "").strip().rstrip("/")
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
        raise AppIdentityBindingUnavailable("Generated-app identity gateway URL is not configured")
    if environment in {"production", "prod"} and parsed.scheme != "https":
        raise AppIdentityBindingUnavailable("Generated-app identity gateway must use HTTPS in production")
    return configured


def attach_app_identity_grants(submission):
    requests = app_identity_binding_requests(submission)
    if not requests:
        return submission
    if len(requests) != 1 or requests[0].semanticName != APP_IDENTITY_BINDING_NAME:
        raise AppIdentityBindingUnavailable(
            f"Generated-app identity requires exactly one semantic binding named {APP_IDENTITY_BINDING_NAME}"
        )
    try:
        configured_app_data_url()
        identity_secret()  # Runtime user sessions must never fall back to Operly account auth.
        gateway = _gateway_url()
        ttl = max(900, int(submission.resources.previewSeconds) + 900)
        bindings = []
        for request in submission.serviceBindings:
            if request.capabilityId != APP_IDENTITY_CAPABILITY_ID:
                bindings.append(request)
                continue
            token = issue_capability_grant(
                submission.workspaceId,
                submission.applicationId,
                capability_id=APP_IDENTITY_CAPABILITY_ID,
                scopes=("auth",),
                allowed_scopes=frozenset({"auth"}),
                ttl_seconds=ttl,
            )
            bindings.append(
                request.model_copy(
                    update={
                        "transport": ServiceBindingTransport(
                            gatewayUrl=gateway,
                            runtimeToken=token,
                        )
                    }
                )
            )
        return submission.model_copy(update={"serviceBindings": bindings})
    except (BindingGrantError, RuntimeError, ValueError) as error:
        raise AppIdentityBindingUnavailable(str(error)) from error


__all__ = [
    "AppIdentityBindingUnavailable",
    "app_identity_binding_requests",
    "attach_app_identity_grants",
]
