from __future__ import annotations

import json
import os
import secrets
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.database.models import TenantMember
from packages.database.principal_models import ClientGrant, Principal
from packages.mcp.gateway import McpGateway, McpGatewayError, McpRequestContext, narrow_scope_rules
from packages.mcp.oauth import (
    ACCESS_TOKEN_TTL_SECONDS,
    McpOAuthError,
    consume_authorization_code,
    decode_access_token,
    decode_refresh_token,
    issue_access_token,
    issue_authorization_code,
    issue_refresh_token,
)
from packages.security.principals import PrincipalService


router = APIRouter(tags=["mcp"])
gateway = McpGateway()
CHATGPT_CLIENT_ID = "chatgpt"
MCP_PROTOCOL_VERSION = "2026-07-28"
LEGACY_MCP_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = {MCP_PROTOCOL_VERSION, LEGACY_MCP_PROTOCOL_VERSION}


def _public_base_url(request: Request) -> str:
    configured = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    return configured or str(request.base_url).rstrip("/")


def _resource(request: Request) -> str:
    return f"{_public_base_url(request)}/mcp"


def _client_secret() -> str:
    return os.getenv("CHATGPT_MCP_CLIENT_SECRET", "").strip()


def _allowed_redirect_uris() -> set[str]:
    return {
        item.strip()
        for item in os.getenv("CHATGPT_MCP_REDIRECT_URIS", "").split(",")
        if item.strip()
    }


def _verify_client(client_id: str, client_secret: str | None) -> None:
    if client_id != CHATGPT_CLIENT_ID:
        raise HTTPException(401, "Invalid MCP OAuth client")
    expected = _client_secret()
    # PKCE + exact redirect binding allows ChatGPT to operate as a public OAuth
    # client when no secret is configured. If Operly has a client secret, require it.
    if expected:
        if not client_secret or not secrets.compare_digest(expected, client_secret):
            raise HTTPException(401, "Invalid MCP OAuth client")


def _redirect_with_query(uri: str, values: dict[str, str]) -> str:
    parsed = urlsplit(uri)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(values)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _oauth_challenge(request: Request) -> dict[str, str]:
    base = _public_base_url(request)
    return {
        "WWW-Authenticate": f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource"'
    }


def _grant_scopes(grant: ClientGrant) -> frozenset[str]:
    try:
        values = json.loads(grant.scopes_json or "[]")
    except (TypeError, json.JSONDecodeError):
        return frozenset()
    return frozenset(str(item).strip().lower() for item in values if str(item).strip())


async def _active_grant(db: AsyncSession, grant_id: str, *, client_id: str = CHATGPT_CLIENT_ID) -> ClientGrant:
    row = await db.scalar(
        select(ClientGrant).where(
            ClientGrant.id == grant_id,
            ClientGrant.client_id == client_id,
            ClientGrant.status == "active",
        )
    )
    if row is None or row.tenant_id is None:
        raise HTTPException(401, "MCP grant is no longer active")
    if row.expires_at is not None and row.expires_at <= datetime.utcnow():
        raise HTTPException(401, "MCP grant has expired")
    return row


@router.get("/.well-known/oauth-authorization-server")
async def oauth_metadata(request: Request):
    base = _public_base_url(request)
    auth_methods = ["client_secret_post"] if _client_secret() else ["none"]
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": auth_methods,
        "scopes_supported": ["workspace:*", "offline_access"],
        "authorization_response_iss_parameter_supported": True,
    }


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
async def protected_resource_metadata(request: Request):
    base = _public_base_url(request)
    return {
        "resource": _resource(request),
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["workspace:*"],
    }


@router.get("/oauth/authorize")
async def authorize_chatgpt(
    request: Request,
    response_type: str = Query(...),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    resource: str = Query(...),
    state: str | None = Query(default=None),
    scope: str = Query(default=""),
    code_challenge: str = Query(..., min_length=43, max_length=128),
    code_challenge_method: str = Query(...),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    if response_type != "code" or client_id != CHATGPT_CLIENT_ID:
        raise HTTPException(400, "Unsupported OAuth request")
    if resource != _resource(request):
        raise HTTPException(400, "OAuth resource does not match the Operly MCP server")
    if code_challenge_method != "S256":
        raise HTTPException(400, "PKCE S256 is required")
    allowed_redirects = _allowed_redirect_uris()
    if not allowed_redirects or redirect_uri not in allowed_redirects:
        raise HTTPException(400, "OAuth redirect URI is not allowed")
    if auth.role != "owner":
        raise HTTPException(403, "Only the workspace owner can authorize an external AI client")

    principal = await PrincipalService.user_principal(db, auth.user.id)
    grant = await db.scalar(
        select(ClientGrant).where(
            ClientGrant.principal_id == principal.id,
            ClientGrant.tenant_id == auth.tenant.id,
            ClientGrant.client_id == CHATGPT_CLIENT_ID,
            ClientGrant.status == "active",
        )
    )
    if grant is None:
        raise HTTPException(403, "Enable ChatGPT MCP access in Operly before connecting it")
    if grant.expires_at is not None and grant.expires_at <= datetime.utcnow():
        raise HTTPException(403, "The ChatGPT MCP grant has expired")

    granted_scopes = _grant_scopes(grant)
    requested = frozenset(item.strip().lower() for item in scope.split() if item and item != "offline_access")
    if requested:
        narrowed = narrow_scope_rules(requested, granted_scopes)
        if narrowed != requested:
            raise HTTPException(403, "The MCP client requested capabilities outside the Operly grant")
        effective_scopes = sorted(requested)
    else:
        effective_scopes = sorted(granted_scopes)

    code = await issue_authorization_code(
        db,
        grant_id=grant.id,
        principal_id=principal.id,
        tenant_id=auth.tenant.id,
        client_id=CHATGPT_CLIENT_ID,
        redirect_uri=redirect_uri,
        scopes=effective_scopes,
        code_challenge=code_challenge,
        resource=resource,
    )
    await db.commit()
    params = {"code": code, "iss": _public_base_url(request)}
    if state is not None:
        params["state"] = state
    return RedirectResponse(_redirect_with_query(redirect_uri, params), status_code=302)


@router.post("/oauth/token")
async def oauth_token(
    request: Request,
    grant_type: str = Form(...),
    client_id: str = Form(...),
    resource: str | None = Form(default=None),
    client_secret: str | None = Form(default=None),
    code: str | None = Form(default=None),
    redirect_uri: str | None = Form(default=None),
    code_verifier: str | None = Form(default=None),
    refresh_token: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
):
    _verify_client(client_id, client_secret)
    effective_resource = resource or _resource(request)
    if effective_resource != _resource(request):
        raise HTTPException(400, "OAuth resource does not match the Operly MCP server")
    try:
        if grant_type == "authorization_code":
            if not code or not redirect_uri or not code_verifier:
                raise HTTPException(400, "Incomplete authorization-code exchange")
            payload = await consume_authorization_code(
                db,
                code,
                client_id=client_id,
                redirect_uri=redirect_uri,
                code_verifier=code_verifier,
                resource=effective_resource,
            )
        elif grant_type == "refresh_token":
            if not refresh_token:
                raise HTTPException(400, "Missing refresh token")
            payload = decode_refresh_token(refresh_token)
            if payload.get("client_id") != client_id or payload.get("resource") != effective_resource:
                raise HTTPException(401, "Refresh token client or resource mismatch")
        else:
            raise HTTPException(400, "Unsupported grant type")
    except McpOAuthError as error:
        raise HTTPException(401, str(error)) from error

    grant = await _active_grant(db, str(payload.get("grant_id", "")), client_id=client_id)
    if grant.principal_id != payload.get("principal_id") or grant.tenant_id != payload.get("tenant_id"):
        raise HTTPException(401, "MCP grant identity mismatch")
    token_scopes = sorted(narrow_scope_rules(payload.get("scopes") or [], _grant_scopes(grant)))
    token_payload = {
        "grant_id": grant.id,
        "principal_id": grant.principal_id,
        "tenant_id": grant.tenant_id,
        "client_id": client_id,
        "resource": effective_resource,
        "scopes": token_scopes,
    }
    access_token = issue_access_token(token_payload)
    next_refresh = issue_refresh_token(token_payload)
    await db.commit()
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_TTL_SECONDS,
        "refresh_token": next_refresh,
        "scope": " ".join(token_scopes + ["offline_access"]),
    }


async def _request_context(
    request: Request,
    authorization: str | None,
    db: AsyncSession,
) -> McpRequestContext:
    challenge = _oauth_challenge(request)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Bearer token required", headers=challenge)
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_access_token(token)
    except McpOAuthError as error:
        raise HTTPException(401, str(error), headers=challenge) from error
    client_id = str(payload.get("client_id") or "")
    if client_id != CHATGPT_CLIENT_ID or payload.get("resource") != _resource(request):
        raise HTTPException(401, "Invalid MCP client or token audience", headers=challenge)

    try:
        grant = await _active_grant(db, str(payload.get("grant_id", "")), client_id=client_id)
    except HTTPException as error:
        if error.status_code == 401:
            error.headers = challenge
        raise
    if grant.principal_id != payload.get("principal_id") or grant.tenant_id != payload.get("tenant_id"):
        raise HTTPException(401, "MCP token identity mismatch", headers=challenge)
    principal = await db.get(Principal, grant.principal_id)
    if principal is None or not principal.user_id or principal.status != "active":
        raise HTTPException(401, "MCP principal is unavailable", headers=challenge)
    membership = await db.scalar(
        select(TenantMember).where(
            TenantMember.user_id == principal.user_id,
            TenantMember.tenant_id == grant.tenant_id,
        )
    )
    if membership is None:
        raise HTTPException(403, "Workspace membership no longer exists")

    effective_scopes = narrow_scope_rules(payload.get("scopes") or [], _grant_scopes(grant))
    return McpRequestContext(
        tenant_id=str(grant.tenant_id),
        user_id=principal.user_id,
        client_id=client_id,
        grant_id=grant.id,
        objective="External AI request through Operly MCP",
        token_scopes=effective_scopes,
        conversation_id=f"mcp:{client_id}:{grant.id}",
    )


def _rpc_result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _rpc_error(request_id, code: int, message: str, data: dict | None = None):
    error = {"code": code, "message": message}
    if data:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _mcp_response(payload: dict, *, status_code: int = 200, cache: bool = False) -> JSONResponse:
    headers = {
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        "Cache-Control": "private, max-age=5" if cache else "no-store",
    }
    return JSONResponse(payload, status_code=status_code, headers=headers)


def _server_discovery() -> dict:
    return {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "serverInfo": {"name": "operly", "version": "0.7.0-mcp"},
        "capabilities": {"tools": {"listChanged": False}},
        "instructions": (
            "Operly exposes only the current authenticated principal's governed Workspace capabilities. "
            "Client grants narrow visibility but never add authority. Human approvals remain mandatory."
        ),
    }


@router.post("/mcp")
async def mcp_endpoint(
    request: Request,
    payload: dict,
    authorization: str | None = Header(default=None),
    protocol_version: str | None = Header(default=None, alias="MCP-Protocol-Version"),
    routed_method: str | None = Header(default=None, alias="Mcp-Method"),
    routed_name: str | None = Header(default=None, alias="Mcp-Name"),
    db: AsyncSession = Depends(get_db),
):
    request_id = payload.get("id")
    if protocol_version and protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
        return _mcp_response(_rpc_error(request_id, -32600, "Unsupported MCP protocol version"), status_code=400)

    body_method = str(payload.get("method") or "").strip()
    header_method = str(routed_method or "").strip()
    if body_method and header_method and body_method != header_method:
        return _mcp_response(
            _rpc_error(request_id, -32600, "MCP method header/body mismatch"),
            status_code=400,
        )
    method = body_method or header_method
    context = await _request_context(request, authorization, db)

    if method == "notifications/initialized":
        return Response(status_code=204, headers={"MCP-Protocol-Version": MCP_PROTOCOL_VERSION})
    if method in {"initialize", "server/discover"}:
        return _mcp_response(_rpc_result(request_id, _server_discovery()))
    if method == "ping":
        return _mcp_response(_rpc_result(request_id, {}))

    if method == "tools/list":
        definitions = await gateway.list_tools(db, context)
        return _mcp_response(
            _rpc_result(
                request_id,
                {
                    "tools": definitions,
                    "ttlMs": 5000,
                    "cacheScope": "private",
                },
            ),
            cache=True,
        )

    if method == "tools/call":
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            return _mcp_response(_rpc_error(request_id, -32602, "Tool call params must be an object"))
        body_name = str(params.get("name") or "").strip()
        header_name = str(routed_name or "").strip()
        if body_name and header_name and body_name != header_name:
            return _mcp_response(
                _rpc_error(request_id, -32602, "MCP tool name header/body mismatch"),
                status_code=400,
            )
        name = body_name or header_name
        arguments = params.get("arguments") or {}
        meta = params.get("_meta") or {}
        if not isinstance(arguments, dict) or not isinstance(meta, dict):
            return _mcp_response(_rpc_error(request_id, -32602, "Tool arguments and _meta must be objects"))
        try:
            result = await gateway.call_tool(
                db,
                context,
                tool_id=name,
                arguments=arguments,
                goal=str(meta.get("operly/goal") or context.objective),
                request_id=str(meta.get("operly/requestId") or "") or None,
                approval_id=str(meta.get("operly/approvalId") or "") or None,
                conversation_id=str(meta.get("operly/conversationId") or "") or context.conversation_id,
            )
        except McpGatewayError as error:
            result = {
                "ok": False,
                "status": "failed",
                "error": {"code": error.code, "message": str(error)},
                "agent_instruction": "Refresh tools/list and current Workspace state before attempting another action.",
            }

        call_meta = {
            "operly/requestId": result.get("request_id"),
            "operly/runId": result.get("run_id"),
            "operly/approvalId": result.get("approval_id"),
            "operly/status": result.get("status"),
        }
        if result.get("ok"):
            structured = result.get("result") or {}
            body = {
                "content": [{"type": "text", "text": json.dumps(result, default=str)}],
                "structuredContent": structured,
                "_meta": call_meta,
                "isError": False,
            }
        else:
            body = {
                "content": [{"type": "text", "text": json.dumps(result, default=str)}],
                "_meta": call_meta,
                "isError": True,
            }
        return _mcp_response(_rpc_result(request_id, body))

    return _mcp_response(_rpc_error(request_id, -32601, "Method not found"), status_code=404)
