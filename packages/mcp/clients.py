"""Fail-closed pre-registered OAuth clients for Operly's existing MCP endpoint.

No dynamic registration or arbitrary remote metadata fetching. Each client must
be explicitly provisioned by the operator and authorized by the workspace owner.
The resulting grant is scoped to one user, one workspace, and one client.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

_ID = re.compile(r"^[a-z][a-z0-9_-]{1,79}$")
_SECRET_ENV = re.compile(r"^OPERLY_MCP_[A-Z][A-Z0-9_]*_SECRET$")
_MAX_CLIENTS = 20


@dataclass(frozen=True)
class RegisteredMcpClient:
    client_id: str
    name: str
    redirect_uris: frozenset[str]
    secret_env: str | None = None

    @property
    def client_secret(self) -> str:
        if self.client_id == "chatgpt" and self.secret_env is None:
            return os.getenv("CHATGPT_MCP_CLIENT_SECRET", "").strip()
        return os.getenv(self.secret_env, "").strip() if self.secret_env else ""


def _validate_redirect(uri: str) -> str:
    if not isinstance(uri, str) or not 1 <= len(uri) <= 500:
        raise ValueError("Invalid MCP redirect URI")
    parsed = urlsplit(uri)
    if (parsed.username is not None or parsed.password is not None
            or parsed.fragment or parsed.query or not parsed.hostname
            or parsed.hostname.endswith(".") or parsed.port == 0):
        raise ValueError("Invalid MCP redirect URI")
    hostname = parsed.hostname.lower()
    if not (
        parsed.scheme == "https" and hostname not in {"localhost", "127.0.0.1"}
        or parsed.scheme == "http" and hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ValueError("MCP redirects require HTTPS or exact local loopback")
    return uri


def registered_clients() -> dict[str, RegisteredMcpClient]:
    chatgpt_redirects = frozenset(_validate_redirect(s.strip()) for s in
        os.getenv("CHATGPT_MCP_REDIRECT_URIS", "").split(",") if s.strip())
    clients = {
        "chatgpt": RegisteredMcpClient(
            client_id="chatgpt", name="ChatGPT",
            redirect_uris=chatgpt_redirects,
        ),
    }
    raw = os.getenv("OPERLY_MCP_CLIENTS_JSON", "").strip()
    if not raw:
        return clients
    try:
        definitions = json.loads(raw)
    except ValueError as exc:
        raise ValueError("Invalid OPERLY_MCP_CLIENTS_JSON") from exc
    if not isinstance(definitions, dict) or len(definitions) > _MAX_CLIENTS:
        raise ValueError("Invalid MCP registered-client catalog")
    for client_id, spec in definitions.items():
        if (not isinstance(client_id, str) or not _ID.fullmatch(client_id)
                or client_id == "chatgpt" or not isinstance(spec, dict)
                or set(spec) - {"name", "redirect_uris", "secret_env"}):
            raise ValueError("Invalid MCP client definition")
        uris = spec.get("redirect_uris", [])
        name = spec.get("name", client_id)
        secret_env = spec.get("secret_env")
        if (not isinstance(uris, list) or len(uris) > 12
                or not isinstance(name, str) or not 1 <= len(name) <= 80
                or (secret_env is not None and
                    (not isinstance(secret_env, str)
                     or not _SECRET_ENV.fullmatch(secret_env)))):
            raise ValueError("Invalid MCP client configuration")
        redirects = frozenset(_validate_redirect(uri) for uri in uris)
        if len(redirects) != len(uris):
            raise ValueError("Duplicate MCP redirect URI")
        clients[client_id] = RegisteredMcpClient(
            client_id=client_id, name=name,
            redirect_uris=redirects, secret_env=secret_env,
        )
    return clients


def get_registered_client(client_id: str) -> RegisteredMcpClient | None:
    return registered_clients().get(client_id)


def external_client_catalog() -> list[dict]:
    return [
        {"id": client.client_id, "name": client.name, "surface": "mcp",
         "owner_managed": True, "configured": bool(client.redirect_uris)}
        for client in registered_clients().values()
    ]
