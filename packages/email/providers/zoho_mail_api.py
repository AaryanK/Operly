import asyncio
import os
import time
from urllib.parse import urlparse

import httpx

from packages.email.providers.base import EmailEnvelope


ZOHO_ACCOUNTS_HOSTS = {
    "accounts.zoho.com",
    "accounts.zoho.eu",
    "accounts.zoho.in",
    "accounts.zoho.com.au",
    "accounts.zoho.jp",
    "accounts.zohocloud.ca",
    "accounts.zoho.com.cn",
    "accounts.zoho.ae",
    "accounts.zoho.sa",
}
ZOHO_MAIL_HOSTS = {
    "mail.zoho.com",
    "mail.zoho.eu",
    "mail.zoho.in",
    "mail.zoho.com.au",
    "mail.zoho.jp",
    "mail.zohocloud.ca",
    "mail.zoho.com.cn",
    "mail.zoho.ae",
    "mail.zoho.sa",
}


class ZohoMailConfigurationError(RuntimeError):
    pass


class ZohoMailAPIError(RuntimeError):
    pass


def _validated_base_url(
    value: str,
    *,
    setting: str,
    allowed_hosts: set[str],
    expected_path: str,
) -> str:
    parsed = urlparse(value.strip().rstrip("/"))
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    try:
        port = parsed.port
    except ValueError as error:
        raise ZohoMailConfigurationError(
            f"{setting} must use the documented HTTPS endpoint for your Zoho data center"
        ) from error
    if (
        parsed.scheme != "https"
        or host not in allowed_hosts
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or port not in {None, 443}
        or path != expected_path
    ):
        raise ZohoMailConfigurationError(
            f"{setting} must use the documented HTTPS endpoint for your Zoho data center"
        )
    return f"https://{host}{expected_path}"


class ZohoMailAPIProvider:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.account_id = os.getenv("ZOHO_MAIL_ACCOUNT_ID", "").strip()
        self.client_id = os.getenv("ZOHO_MAIL_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("ZOHO_MAIL_CLIENT_SECRET", "")
        self.refresh_token = os.getenv("ZOHO_MAIL_REFRESH_TOKEN", "").strip()
        self.from_email = os.getenv("OPERLY_FROM_EMAIL", "").strip()
        self.accounts_base_url = _validated_base_url(
            os.getenv("ZOHO_ACCOUNTS_BASE_URL", "https://accounts.zoho.com"),
            setting="ZOHO_ACCOUNTS_BASE_URL",
            allowed_hosts=ZOHO_ACCOUNTS_HOSTS,
            expected_path="",
        )
        self.mail_api_base_url = _validated_base_url(
            os.getenv("ZOHO_MAIL_API_BASE_URL", "https://mail.zoho.com/api"),
            setting="ZOHO_MAIL_API_BASE_URL",
            allowed_hosts=ZOHO_MAIL_HOSTS,
            expected_path="/api",
        )
        self.timeout = self._validated_timeout()
        self._transport = transport
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0
        self._token_lock = asyncio.Lock()
        self._validate_configuration()

    @staticmethod
    def _validated_timeout() -> float:
        value = os.getenv("ZOHO_MAIL_TIMEOUT_SECONDS", "15").strip()
        try:
            timeout = float(value)
        except ValueError as error:
            raise ZohoMailConfigurationError(
                "ZOHO_MAIL_TIMEOUT_SECONDS must be a number"
            ) from error
        if not 1 <= timeout <= 30:
            raise ZohoMailConfigurationError(
                "ZOHO_MAIL_TIMEOUT_SECONDS must be between 1 and 30"
            )
        return timeout

    def _validate_configuration(self) -> None:
        missing = [
            name
            for name, value in (
                ("ZOHO_MAIL_ACCOUNT_ID", self.account_id),
                ("ZOHO_MAIL_CLIENT_ID", self.client_id),
                ("ZOHO_MAIL_CLIENT_SECRET", self.client_secret),
                ("ZOHO_MAIL_REFRESH_TOKEN", self.refresh_token),
                ("OPERLY_FROM_EMAIL", self.from_email),
            )
            if not value
        ]
        if missing:
            raise ZohoMailConfigurationError(
                "Zoho Mail API is not configured: " + ", ".join(missing)
            )
        if not self.account_id.isdigit():
            raise ZohoMailConfigurationError("ZOHO_MAIL_ACCOUNT_ID must be numeric")
        if "@" not in self.from_email or len(self.from_email) > 320:
            raise ZohoMailConfigurationError("OPERLY_FROM_EMAIL must be a valid email address")

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout, transport=self._transport)

    def _cached_token_is_usable(self, *, rejected_token: str | None = None) -> bool:
        return bool(
            self._access_token
            and self._access_token != rejected_token
            and time.monotonic() + 60 < self._access_token_expires_at
        )

    async def _get_access_token(self, *, rejected_token: str | None = None) -> str:
        if self._cached_token_is_usable(rejected_token=rejected_token):
            return self._access_token or ""
        async with self._token_lock:
            if self._cached_token_is_usable(rejected_token=rejected_token):
                return self._access_token or ""
            try:
                async with self._client() as client:
                    response = await client.post(
                        f"{self.accounts_base_url}/oauth/v2/token",
                        data={
                            "client_id": self.client_id,
                            "client_secret": self.client_secret,
                            "refresh_token": self.refresh_token,
                            "grant_type": "refresh_token",
                        },
                        headers={"Accept": "application/json"},
                    )
                    response.raise_for_status()
                    payload = response.json()
            except (httpx.HTTPError, ValueError) as error:
                raise ZohoMailAPIError("Zoho OAuth token refresh failed") from error
            token = str(payload.get("access_token") or "").strip()
            if not token:
                raise ZohoMailAPIError("Zoho OAuth token refresh failed")
            try:
                expires_in = int(payload.get("expires_in", 3600))
            except (TypeError, ValueError):
                expires_in = 3600
            self._access_token = token
            self._access_token_expires_at = time.monotonic() + max(60, min(expires_in, 86400))
            return token

    async def _post_message(self, envelope: EmailEnvelope, token: str) -> httpx.Response:
        try:
            async with self._client() as client:
                return await client.post(
                    f"{self.mail_api_base_url}/accounts/{self.account_id}/messages",
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Zoho-oauthtoken {token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "fromAddress": self.from_email,
                        "toAddress": envelope.to_email,
                        "subject": envelope.subject,
                        "content": envelope.html_body,
                        "mailFormat": "html",
                        "encoding": "UTF-8",
                        "askReceipt": "no",
                    },
                )
        except httpx.HTTPError as error:
            raise ZohoMailAPIError("Zoho Mail delivery failed") from error

    async def send(self, envelope: EmailEnvelope) -> None:
        token = await self._get_access_token()
        response = await self._post_message(envelope, token)
        if response.status_code == 401:
            token = await self._get_access_token(rejected_token=token)
            response = await self._post_message(envelope, token)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise ZohoMailAPIError("Zoho Mail delivery failed") from error
