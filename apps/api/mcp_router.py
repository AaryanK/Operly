import json
import os
import secrets
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.database.models import TenantMember
from packages.database.principal_models import ClientGrant, Principal
from packages.mcp.gateway import McpGateway, McpRequestContext
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
MCP_PROTOCOL_VERSION = "2025-06-18"


def _public_base_url(request: Request) -> str:
    configured = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


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
    expected = _client_secret()
    if client_id != CHATGPT_CLIENT_ID or not expected or not client_secret:
        raise HTTPException(401, "Invalid MCP OAuth client")
    if not secrets.compare_digest(expected, client_secret):
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


async def _active_grant(db: AsyncSession, grant_id: str) -> ClientGrant:
    row = await db.scalar(
        select(ClientGrant).where(
            ClientGrant.id == grant_id,
            ClientGrant.client_id == CHATGPT_CLIENT_ID,
            ClientGrant.status == "active",
        )
    )
    if row is None or row.tenant_id is None:
        raise HTTPException(401, "MCP grant is no longer active")
    return row


@router.get("/.well-known/oauth-authorization-server")
async def oauth_metadata(request: Request):
    base = _public_base_url(request)
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_post"],
        "scopes_supported": ["offline_access"],
    }


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
async def protected_resource_metadata(request: Request):
    base = _public_base_url(request)
    return {
        "resource": _resource(request),
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
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
    code_challenge: str = Query(...),
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
        raise HTTPException(403, "Only the workspace owner can authorize ChatGPT")

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
        raise HTTPException(403, "Enable ChatGPT access in Operly before connecting it")

    try:
        granted_scopes = {str(item) for item in json.loads(grant.scopes_json or "[]")}
    except (TypeError, json.JSONDecodeError):
        granted_scopes = set()
    requested = {item for item in scope.split() if item and item != "offline_access"}
    if requested and not requested.issubset(granted_scopes):
        raise HTTPException(403, "ChatGPT requested permissions not granted by the owner")
    effective_scopes = sorted(requested or granted_scopes)

    code = issue_authorization_code(
        grant_id=grant.id,
        principal_id=principal.id,
        tenant_id=auth.tenant.id,
        client_id=CHATGPT_CLIENT_ID,
        redirect_uri=redirect_uri,
        scopes=effective_scopes,
        code_challenge=code_challenge,
        resource=resource,
    )
    params = {"code": code}
    if state is not None:
        params["state"] = state
    return RedirectResponse(_redirect_with_query(redirect_uri, params), status_code=302)


@router.post("/oauth/token")
async def oauth_token(
    request: Request,
    grant_type: str = Form(...),
    client_id: str = Form(...),
    resource: str = Form(...),
    client_secret: str | None = Form(default=None),
    code: str | None = Form(default=None),
    redirect_uri: str | None = Form(default=None),
    code_verifier: str | None = Form(default=None),
    refresh_token: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
):
    _verify_client(client_id, client_secret)
    if resource != _resource(request):
        raise HTTPException(400, "OAuth resource does not match the Operly MCP server")
    try:
        if grant_type == "authorization_code":
            if not code or not redirect_uri or not code_verifier:
                raise HTTPException(400, "Incomplete authorization-code exchange")
            payload = consume_authorization_code(
                code,
                client_id=client_id,
                redirect_uri=redirect_uri,
                code_verifier=code_verifier,
                resource=resource,
            )
        elif grant_type == "refresh_token":
            if not refresh_token:
                raise HTTPException(400, "Missing refresh token")
            payload = decode_refresh_token(refresh_token)
            if payload.get("client_id") != client_id or payload.get("resource") != resource:
                raise HTTPException(401, "Refresh token client or resource mismatch")
        else:
            raise HTTPException(400, "Unsupported grant type")
    except McpOAuthError as exc:
        raise HTTPException(401, str(exc)) from exc

    grant = await _active_grant(db, str(payload.get("grant_id", "")))
    if grant.principal_id != payload.get("principal_id") or grant.tenant_id != payload.get("tenant_id"):
        raise HTTPException(401, "MCP grant identity mismatch")
    try:
        current_scopes = {str(item) for item in json.loads(grant.scopes_json or "[]")}
    except (TypeError, json.JSONDecodeError):
        current_scopes = set()
    token_scopes = sorted(set(payload.get("scopes") or []).intersection(current_scopes))
    token_payload = {
        "grant_id": grant.id,
        "principal_id": grant.principal_id,
        "tenant_id": grant.tenant_id,
        "client_id": CHATGPT_CLIENT_ID,
        "resource": resource,
        "scopes": token_scopes,
    }
    return {
        "access_token": issue_access_token(token_payload),
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_TTL_SECONDS,
        "refresh_token": issue_refresh_token(token_payload),
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
    except McpOAuthError as exc:
        raise HTTPException(401, str(exc), headers=challenge) from exc
    if payload.get("client_id") != CHATGPT_CLIENT_ID or payload.get("resource") != _resource(request):
        raise HTTPException(401, "Invalid MCP client or token audience", headers=challenge)

    try:
        grant = await _active_grant(db, str(payload.get("grant_id", "")))
    except HTTPException as exc:
        if exc.status_code == 401:
            exc.headers = challenge
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
    return McpRequestContext(
        tenant_id=str(grant.tenant_id),
        user_id=principal.user_id,
        role=membership.role,
        client_id=CHATGPT_CLIENT_ID,
        objective="ChatGPT MCP request",
        token_scopes=set(payload.get("scopes") or []),
    )


def _rpc_result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _rpc_error(request_id, code: int, message: str):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


@router.post("/mcp")
async def mcp_endpoint(
    request: Request,
    payload: dict,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    context = await _request_context(request, authorization, db)
    method = payload.get("method")
    request_id = payload.get("id")

    if method == "notifications/initialized":
        return Response(status_code=204)
    if method == "initialize":
        return JSONResponse(
            _rpc_result(
                request_id,
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "operly", "version": "0.1.0"},
                },
            )
        )
    if method == "ping":
        return JSONResponse(_rpc_result(request_id, {}))
    if method == "tools/list":
        definitions = await gateway.list_tools(context)
        tools = [
            {
                "name": item["name"],
                "description": item.get("description", ""),
                "inputSchema": item.get("parameters") or {"type": "object", "properties": {}},
            }
            for item in definitions
        ]
        return JSONResponse(_rpc_result(request_id, {"tools": tools}))
    if method == "tools/call":
        params = payload.get("params") or {}
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return JSONResponse(_rpc_error(request_id, -32602, "Tool arguments must be an object"))
        result = await gateway.call_tool(
            context,
            tool_id=name,
            arguments=arguments,
            call_id=str(request_id) if request_id is not None else None,
        )
        return JSONResponse(
            _rpc_result(
                request_id,
                {
                    "content": [{"type": "text", "text": json.dumps(result, default=str)}],
                    "isError": not bool(result.get("ok", True)),
                },
            )
        )
    return JSONResponse(_rpc_error(request_id, -32601, "Method not found"), status_code=404)
