"""Security-contract tests for optional Discord -> Android PKCE handoff.

These tests use no Discord credentials and never create a real session.
"""
import base64
import hashlib
from unittest.mock import patch

from starlette.requests import Request

from apps.api import discord_mobile_auth as mobile


def _request(cookie_value):
    return Request({"type": "http", "method": "GET", "path": "/api/identities/discord/callback",
                    "headers": [(b"cookie", f"{mobile.MOBILE_COOKIE}={cookie_value}".encode())],
                    "server": ("dragonzpyder.xyz", 443), "scheme": "https",
                    "query_string": b""})


def test_mobile_flow_requires_explicit_flag_and_exact_first_party_callback(monkeypatch):
    monkeypatch.setenv("DISCORD_MOBILE_AUTH_ENABLED", "1")
    monkeypatch.delenv("OPERLY_IDENTITY_OWNER", raising=False)
    with patch("apps.api.discord_auth_router._config",
               return_value=("client-id", "secret", "https://other.example/api/identities/discord/callback")):
        assert mobile.enabled() is False
    with patch("apps.api.discord_auth_router._config",
               return_value=("client-id", "secret", mobile.PUBLIC_CALLBACK)):
        assert mobile.enabled() is True
        monkeypatch.setenv("OPERLY_IDENTITY_OWNER", "dragonzpyder")
        assert mobile.enabled() is False


def test_pkce_and_state_are_strict_and_not_interchangeable():
    verifier=base64.urlsafe_b64encode(b"v"*32).rstrip(b"=").decode()
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert mobile.valid_start_values(verifier,challenge)
    assert not mobile.valid_start_values("short",challenge)
    assert not mobile.valid_start_values(verifier,"short")
    assert not mobile.valid_start_values(verifier,"a"*43+"@")
    assert mobile._challenge_hash(verifier) != mobile._challenge_hash(challenge)
    assert mobile._pkce_hash(challenge) != mobile._challenge_hash(challenge)


def test_mobile_callback_requires_matching_http_only_browser_state():
    state="A"*43
    assert mobile.is_mobile_callback(_request(state),state)
    assert not mobile.is_mobile_callback(_request("B"*43),state)
    assert not mobile.is_mobile_callback(_request(state),None)


def test_only_one_explicit_mobile_redeem_route():
    paths={(tuple(sorted(route.methods)),route.path) for route in mobile.router.routes}
    assert (("GET",),"/api/identities/discord/mobile/start") in paths
    assert (("POST",),"/api/identities/discord/mobile/redeem") in paths
    assert not any("token" in path for _,path in paths)
