import os

from fastapi import Request, Response


SESSION_MAX_AGE = 60 * 60 * 24 * 7
DEV_SESSION_COOKIE = "operly_session"
PROD_SESSION_COOKIE = "__Host-operly_session"
DEV_CSRF_COOKIE = "operly_csrf"
PROD_CSRF_COOKIE = "__Host-operly_csrf"
PREAUTH_CSRF_COOKIE = "operly_preauth_csrf"
GOOGLE_NONCE_COOKIE = "operly_google_nonce"


def production() -> bool:
    return os.getenv("OPERLY_ENV", os.getenv("APP_ENV", "development")).lower() in {
        "production",
        "prod",
    }


def session_cookie_name() -> str:
    return PROD_SESSION_COOKIE if production() else DEV_SESSION_COOKIE


def csrf_cookie_name() -> str:
    return PROD_CSRF_COOKIE if production() else DEV_CSRF_COOKIE


def session_secret_from_request(request: Request) -> str | None:
    return request.cookies.get(PROD_SESSION_COOKIE) or request.cookies.get(DEV_SESSION_COOKIE)


def csrf_secret_from_request(request: Request) -> str | None:
    return request.cookies.get(PROD_CSRF_COOKIE) or request.cookies.get(DEV_CSRF_COOKIE)


def set_session_cookies(response: Response, session_secret: str, csrf_secret: str) -> None:
    secure = production() or os.getenv("PUBLIC_BASE_URL", "").lower().startswith("https://")
    response.set_cookie(
        session_cookie_name(),
        session_secret,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        csrf_cookie_name(),
        csrf_secret,
        max_age=SESSION_MAX_AGE,
        httponly=False,
        secure=secure,
        samesite="strict",
        path="/",
    )
    response.delete_cookie(PREAUTH_CSRF_COOKIE, path="/")
    response.delete_cookie(GOOGLE_NONCE_COOKIE, path="/")


def set_preauth_csrf_cookie(response: Response, secret: str) -> None:
    secure = production() or os.getenv("PUBLIC_BASE_URL", "").lower().startswith("https://")
    response.set_cookie(
        PREAUTH_CSRF_COOKIE,
        secret,
        max_age=60 * 60,
        httponly=False,
        secure=secure,
        samesite="strict",
        path="/",
    )


def set_google_nonce_cookie(response: Response, nonce: str) -> None:
    secure = production() or os.getenv("PUBLIC_BASE_URL", "").lower().startswith("https://")
    response.set_cookie(
        GOOGLE_NONCE_COOKIE,
        nonce,
        max_age=10 * 60,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    for name in (
        DEV_SESSION_COOKIE,
        PROD_SESSION_COOKIE,
        DEV_CSRF_COOKIE,
        PROD_CSRF_COOKIE,
        PREAUTH_CSRF_COOKIE,
        GOOGLE_NONCE_COOKIE,
    ):
        response.delete_cookie(name, path="/")
