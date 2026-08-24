from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import aiohttp

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider


_MAX_BYTES = 1_000_000
_MAX_TEXT = 80_000
_MAX_REDIRECTS = 4
_ALLOWED_CONTENT_TYPES = {
    "text/html",
    "text/plain",
    "application/xhtml+xml",
    "application/xml",
    "text/xml",
    "application/rss+xml",
    "application/atom+xml",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._ignored += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._ignored:
            self._ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored:
            value = " ".join(data.split())
            if value:
                self.parts.append(value)

    def text(self) -> str:
        return "\n".join(self.parts)


class _PinnedResolver(aiohttp.abc.AbstractResolver):
    """Pin one validated hostname to the public addresses we inspected."""

    def __init__(self, hostname: str, addresses: list[str]) -> None:
        self.hostname = hostname
        self.addresses = addresses

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
        if host != self.hostname:
            raise OSError("unexpected hostname")
        return [
            {
                "hostname": host,
                "host": address,
                "port": port,
                "family": socket.AF_INET6 if ":" in address else socket.AF_INET,
                "proto": 0,
                "flags": socket.AI_NUMERICHOST,
            }
            for address in self.addresses
        ]

    async def close(self) -> None:
        return None


def _validate_url(value: str) -> tuple[str, str]:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("public_http_url_required")
    if parsed.username or parsed.password:
        raise ValueError("url_credentials_not_allowed")
    return url, parsed.hostname


async def _public_addresses(hostname: str, port: int) -> list[str]:
    try:
        rows = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            port,
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
        )
    except OSError as error:
        raise ValueError("hostname_resolution_failed") from error
    addresses: list[str] = []
    for row in rows:
        address = str(row[4][0])
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("non_public_network_target")
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise ValueError("hostname_resolution_failed")
    return addresses


async def fetch_public_text(url: str) -> dict:
    """Fetch bounded public text while pinning DNS to prevent private-network access."""
    current, _ = _validate_url(url)
    for redirect_index in range(_MAX_REDIRECTS + 1):
        current, hostname = _validate_url(current)
        parsed = urlparse(current)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = await _public_addresses(hostname, port)
        resolver = _PinnedResolver(hostname, addresses)
        connector = aiohttp.TCPConnector(resolver=resolver, ttl_dns_cache=0)
        timeout = aiohttp.ClientTimeout(total=18, connect=7, sock_read=12)
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            trust_env=False,
            headers={"User-Agent": "OperlyWebReader/1.0"},
        ) as session:
            async with session.get(current, allow_redirects=False) as response:
                if 300 <= response.status < 400 and response.headers.get("Location"):
                    if redirect_index >= _MAX_REDIRECTS:
                        raise ValueError("too_many_redirects")
                    current = urljoin(current, response.headers["Location"])
                    continue
                if response.status < 200 or response.status >= 300:
                    raise ValueError(f"http_status_{response.status}")
                content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                if content_type and content_type not in _ALLOWED_CONTENT_TYPES and not content_type.startswith("text/"):
                    raise ValueError("unsupported_content_type")
                body = bytearray()
                async for chunk in response.content.iter_chunked(32_768):
                    body.extend(chunk)
                    if len(body) > _MAX_BYTES:
                        raise ValueError("response_too_large")
                raw = bytes(body)
                charset = response.charset or "utf-8"
                try:
                    decoded = raw.decode(charset, errors="replace")
                except LookupError:
                    decoded = raw.decode("utf-8", errors="replace")
                if "html" in content_type:
                    parser = _TextExtractor()
                    parser.feed(decoded)
                    text = parser.text()
                else:
                    text = decoded
                text = text.strip()[:_MAX_TEXT]
                return {
                    "url": str(url),
                    "final_url": str(response.url),
                    "status": response.status,
                    "content_type": content_type or None,
                    "text": text,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "truncated": len(decoded) > len(text),
                }
    raise ValueError("too_many_redirects")


class PublicWebReadProvider(BaseProvider):
    name = "operly_public_web"
    capabilities = (
        CapabilityDefinition(
            "web.read_url",
            "web_read_url",
            "Read bounded text from a public HTTP(S) page such as a Blogspot post or feed. Private/link-local network targets, credentials in URLs, oversized responses and non-text payloads are blocked.",
            {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "minLength": 8, "maxLength": 2048},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("workspace:read",),
            approval_policy=ApprovalPolicy.AUTO,
            category="web",
        ),
    )

    async def execute(self, context, capability_name, arguments):
        if capability_name != "web.read_url":
            return CapabilityResult(False, False, {"reason": "unsupported_web_capability"})
        try:
            result = await fetch_public_text(str(arguments.get("url") or ""))
        except (ValueError, aiohttp.ClientError, asyncio.TimeoutError) as error:
            return CapabilityResult(False, False, {"reason": str(error)[:160] or type(error).__name__})
        return CapabilityResult(True, False, result, result["final_url"])

    async def verify(self, context, capability_name, arguments, result):
        return CapabilityResult(result.success, False, result.evidence, result.external_reference)
