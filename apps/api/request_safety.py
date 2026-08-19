import json

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


AUTH_BODY_MAX_BYTES = 20 * 1024


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


class AuthRequestSafetyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method not in {"POST", "PUT", "PATCH"} or not (
            path.startswith("/api/auth/") or path == "/api/session/login"
        ):
            return await call_next(request)
        if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
            return _error(415, "JSON_REQUIRED", "Send this request as JSON")
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > AUTH_BODY_MAX_BYTES:
                    return _error(413, "REQUEST_TOO_LARGE", "This request is too large")
            except ValueError:
                return _error(400, "INVALID_REQUEST", "This request could not be read")
        body = await request.body()
        if len(body) > AUTH_BODY_MAX_BYTES:
            return _error(413, "REQUEST_TOO_LARGE", "This request is too large")
        if not body:
            return _error(400, "INVALID_JSON", "Send a valid JSON request")
        try:
            json.loads(body, object_pairs_hook=_unique_object)
        except DuplicateJSONKey:
            return _error(400, "DUPLICATE_JSON_FIELD", "Each field may be provided only once")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _error(400, "INVALID_JSON", "Send a valid JSON request")
        return await call_next(request)
