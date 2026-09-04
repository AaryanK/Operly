import json
import os

from starlette.responses import JSONResponse


AUTH_BODY_MAX_BYTES = 20 * 1024
DEFAULT_API_BODY_MAX_BYTES = 8 * 1024 * 1024
MIN_API_BODY_MAX_BYTES = 64 * 1024
MAX_API_BODY_MAX_BYTES = 64 * 1024 * 1024
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class DuplicateJSONKey(ValueError):
    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJSONKey(key)
        result[key] = value
    return result


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"detail": {"code": code, "message": message}},
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


def _api_body_limit() -> int:
    raw = os.getenv("OPERLY_API_MAX_BODY_BYTES", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_API_BODY_MAX_BYTES
    except ValueError:
        value = DEFAULT_API_BODY_MAX_BYTES
    return max(MIN_API_BODY_MAX_BYTES, min(value, MAX_API_BODY_MAX_BYTES))


def _body_guarded(path: str, method: str) -> bool:
    return method in UNSAFE_METHODS and (
        path.startswith("/api/") or path in {"/mcp", "/oauth/token"}
    )


def _is_auth_json_path(path: str) -> bool:
    return path.startswith("/api/auth/") or path == "/api/session/login"


def _stable_mcp_request_id(payload: dict) -> str | None:
    if str(payload.get("method") or "").strip() != "tools/call":
        return None
    rpc_id = payload.get("id")
    if rpc_id is None or isinstance(rpc_id, (dict, list)):
        return None
    params = payload.get("params")
    if not isinstance(params, dict):
        return None
    name = str(params.get("name") or "").strip()[:160]
    return f"mcp-rpc:{name}:{str(rpc_id)[:240]}"


def _prepare_mcp_body(raw: bytes) -> bytes:
    if not raw:
        return raw
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, DuplicateJSONKey):
        return raw
    if not isinstance(payload, dict):
        return raw
    stable_id = _stable_mcp_request_id(payload)
    if not stable_id:
        return raw
    params = payload.get("params")
    if not isinstance(params, dict):
        return raw
    meta = params.get("_meta")
    if meta is None:
        meta = {}
        params["_meta"] = meta
    if not isinstance(meta, dict):
        return raw
    if not str(meta.get("operly/requestId") or "").strip():
        meta["operly/requestId"] = stable_id
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class AuthRequestSafetyMiddleware:
    """Bound unsafe request bodies before FastAPI parses them.

    Auth routes additionally require small, duplicate-key-free JSON bodies. MCP tool
    calls inherit a stable idempotency request ID from the JSON-RPC request ID when a
    client does not provide Operly's explicit request ID, so transport retries cannot
    silently become a second mutating invocation.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "GET").upper()
        path = str(scope.get("path") or "")
        if not _body_guarded(path, method):
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers") or []
        }
        auth_path = _is_auth_json_path(path)
        limit = min(_api_body_limit(), AUTH_BODY_MAX_BYTES) if auth_path else _api_body_limit()

        content_length = headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > limit:
                    response = _error(413, "REQUEST_TOO_LARGE", "This request is too large")
                    await response(scope, receive, send)
                    return
            except ValueError:
                response = _error(400, "INVALID_REQUEST", "This request could not be read")
                await response(scope, receive, send)
                return

        chunks: list[bytes] = []
        total = 0
        more_body = True
        while more_body:
            message = await receive()
            if message.get("type") == "http.disconnect":
                return
            if message.get("type") != "http.request":
                continue
            chunk = bytes(message.get("body") or b"")
            total += len(chunk)
            if total > limit:
                response = _error(413, "REQUEST_TOO_LARGE", "This request is too large")
                await response(scope, receive, send)
                return
            chunks.append(chunk)
            more_body = bool(message.get("more_body"))

        body = b"".join(chunks)
        content_type = headers.get("content-type", "").split(";", 1)[0].lower()

        if auth_path:
            if content_type != "application/json":
                response = _error(415, "JSON_REQUIRED", "Send this request as JSON")
                await response(scope, receive, send)
                return
            if not body:
                response = _error(400, "INVALID_JSON", "Send a valid JSON request")
                await response(scope, receive, send)
                return
            try:
                json.loads(body, object_pairs_hook=_unique_object)
            except DuplicateJSONKey:
                response = _error(400, "DUPLICATE_JSON_FIELD", "Each field may be provided only once")
                await response(scope, receive, send)
                return
            except (UnicodeDecodeError, json.JSONDecodeError):
                response = _error(400, "INVALID_JSON", "Send a valid JSON request")
                await response(scope, receive, send)
                return

        if path == "/mcp" and content_type == "application/json":
            body = _prepare_mcp_body(body)

        delivered = False

        async def replay_receive():
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay_receive, send)
