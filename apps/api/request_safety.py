import hashlib
import json
import os
import time
from collections import deque

from starlette.responses import JSONResponse


AUTH_BODY_MAX_BYTES = 20 * 1024
DEFAULT_API_BODY_MAX_BYTES = 8 * 1024 * 1024
MIN_API_BODY_MAX_BYTES = 64 * 1024
MAX_API_BODY_MAX_BYTES = 64 * 1024 * 1024
AUTH_SHIELD_WINDOW_SECONDS = 60.0
AUTH_SHIELD_DEFAULT_LIMIT = 60
MAX_RUNTIME_REQUEST_ID_BYTES = 160
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Cheap process-local first line of defense. The durable DB limiter remains the
# authoritative secondary policy, but repeated hostile requests from one source are
# dropped here before they can amplify into one database insert per attempt.
_auth_shield_hits: dict[str, deque[float]] = {}
_auth_shield_last_prune = 0.0


class DuplicateJSONKey(ValueError):
    pass


class McpRequestSafetyError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


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


def _auth_shield_limit() -> int:
    raw = os.getenv("OPERLY_AUTH_EDGE_REQUESTS_PER_MINUTE", "").strip()
    try:
        value = int(raw) if raw else AUTH_SHIELD_DEFAULT_LIMIT
    except ValueError:
        value = AUTH_SHIELD_DEFAULT_LIMIT
    return max(10, min(value, 600))


def _auth_shield_key(scope, path: str) -> str:
    client = scope.get("client")
    address = str(client[0] if isinstance(client, (tuple, list)) and client else "unknown")
    return f"{address}:{path}"


def _auth_shield_limited(scope, path: str, method: str) -> bool:
    global _auth_shield_last_prune
    if method not in UNSAFE_METHODS or not _is_auth_json_path(path):
        return False
    now = time.monotonic()
    cutoff = now - AUTH_SHIELD_WINDOW_SECONDS
    key = _auth_shield_key(scope, path)
    hits = _auth_shield_hits.setdefault(key, deque())
    while hits and hits[0] < cutoff:
        hits.popleft()
    if len(hits) >= _auth_shield_limit():
        return True
    hits.append(now)

    if now - _auth_shield_last_prune > AUTH_SHIELD_WINDOW_SECONDS:
        _auth_shield_last_prune = now
        for candidate in list(_auth_shield_hits):
            bucket = _auth_shield_hits[candidate]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if not bucket:
                _auth_shield_hits.pop(candidate, None)
        # Fail bounded if a distributed source spray creates many one-off keys.
        if len(_auth_shield_hits) > 20000:
            for candidate in list(_auth_shield_hits)[: len(_auth_shield_hits) - 20000]:
                _auth_shield_hits.pop(candidate, None)
    return False


def _body_guarded(path: str, method: str) -> bool:
    return method in UNSAFE_METHODS and (
        path.startswith("/api/") or path in {"/mcp", "/oauth/token"}
    )


def _is_auth_json_path(path: str) -> bool:
    return path.startswith("/api/auth/") or path == "/api/session/login"


def _mcp_rpc_id(payload: dict):
    rpc_id = payload.get("id")
    if rpc_id is None or isinstance(rpc_id, (dict, list, bool)):
        raise McpRequestSafetyError(
            "MCP_REQUEST_ID_REQUIRED",
            "MCP tools/call requires a scalar JSON-RPC request id",
        )
    if not isinstance(rpc_id, (str, int, float)):
        raise McpRequestSafetyError(
            "MCP_REQUEST_ID_INVALID",
            "MCP JSON-RPC request id must be a string or number",
        )
    return rpc_id


def _stable_mcp_request_id(payload: dict) -> str | None:
    if str(payload.get("method") or "").strip() != "tools/call":
        return None
    rpc_id = _mcp_rpc_id(payload)
    params = payload.get("params")
    if not isinstance(params, dict):
        raise McpRequestSafetyError(
            "MCP_PARAMS_INVALID",
            "MCP tools/call params must be an object",
        )
    name = str(params.get("name") or "").strip()

    # Preserve the pre-existing readable transport identity for ordinary numeric
    # JSON-RPC IDs when it fits the durable schema. This keeps compatible clients and
    # regressions stable without conflating a numeric 42 with the string "42".
    if isinstance(rpc_id, (int, float)) and not isinstance(rpc_id, bool):
        readable = f"mcp-rpc:{name}:{rpc_id}"
        if len(readable.encode("utf-8")) <= MAX_RUNTIME_REQUEST_ID_BYTES:
            return readable

    # String or oversized transport IDs are typed and hashed. The type tag prevents
    # cross-type identity collisions, while hashing guarantees the result fits the
    # approval/request-id columns even for arbitrarily long JSON-RPC identifiers.
    rpc_identity = json.dumps(rpc_id, separators=(",", ":"), ensure_ascii=False)
    typed_identity = f"{type(rpc_id).__name__}:{rpc_identity}"
    digest = hashlib.sha256(f"{name}\0{typed_identity}".encode("utf-8")).hexdigest()
    return f"mcp-rpc:{digest}"


def _prepare_mcp_body(raw: bytes) -> bytes:
    if not raw:
        raise McpRequestSafetyError("INVALID_JSON", "Send a valid MCP JSON request")
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_object)
    except DuplicateJSONKey as error:
        raise McpRequestSafetyError(
            "DUPLICATE_JSON_FIELD",
            "Each MCP JSON field may be provided only once",
        ) from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise McpRequestSafetyError("INVALID_JSON", "Send a valid MCP JSON request") from error
    if not isinstance(payload, dict):
        raise McpRequestSafetyError(
            "MCP_REQUEST_INVALID",
            "MCP requests must be a JSON object",
        )

    if str(payload.get("method") or "").strip() != "tools/call":
        # Parsing above still rejects ambiguous duplicate-key requests for every MCP
        # method. Non-tool calls do not need an Operly idempotency identity.
        return raw

    stable_id = _stable_mcp_request_id(payload)
    params = payload.get("params")
    if not isinstance(params, dict):
        raise McpRequestSafetyError(
            "MCP_PARAMS_INVALID",
            "MCP tools/call params must be an object",
        )
    meta = params.get("_meta")
    if meta is None:
        meta = {}
        params["_meta"] = meta
    if not isinstance(meta, dict):
        raise McpRequestSafetyError(
            "MCP_META_INVALID",
            "MCP tools/call _meta must be an object",
        )

    explicit_request_id = str(meta.get("operly/requestId") or "").strip()
    if explicit_request_id:
        if len(explicit_request_id.encode("utf-8")) > MAX_RUNTIME_REQUEST_ID_BYTES:
            raise McpRequestSafetyError(
                "MCP_REQUEST_ID_TOO_LONG",
                "operly/requestId exceeds the durable request-id limit",
            )
        meta["operly/requestId"] = explicit_request_id
    else:
        meta["operly/requestId"] = stable_id

    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class AuthRequestSafetyMiddleware:
    """Bound unsafe request bodies and reject cheap abuse before route execution.

    Auth routes additionally require small, duplicate-key-free JSON bodies. MCP tool
    calls must be unambiguous JSON and carry either a bounded explicit Operly request
    ID or a deterministic ID derived from a scalar JSON-RPC request ID. This prevents
    transport retries from silently becoming a second mutating invocation.
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

        if _auth_shield_limited(scope, path, method):
            response = JSONResponse(
                {
                    "detail": {
                        "code": "RATE_LIMITED",
                        "message": "Too many attempts. Please wait and try again.",
                    }
                },
                status_code=429,
                headers={"Cache-Control": "no-store", "Retry-After": "60"},
            )
            await response(scope, receive, send)
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

        if path == "/mcp":
            if content_type != "application/json":
                response = _error(415, "JSON_REQUIRED", "Send MCP requests as JSON")
                await response(scope, receive, send)
                return
            try:
                body = _prepare_mcp_body(body)
            except McpRequestSafetyError as error:
                response = _error(error.status_code, error.code, error.message)
                await response(scope, receive, send)
                return

        delivered = False

        async def replay_receive():
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay_receive, send)
