"""Bounded Ollama web-search tools for the OPERLY coding agent.

These tools intentionally sit behind the coding-agent tool registry. The model may
choose to research current framework/API documentation, but web content is returned
as untrusted evidence and never gains filesystem or execution authority.
"""
from __future__ import annotations

import ipaddress
import os
from typing import Any
from urllib.parse import urlparse

import aiohttp


class CodingWebToolError(RuntimeError):
    pass


def _api_key() -> str:
    key = os.getenv("OLLAMA_API_KEY", "").strip()
    if not key:
        raise CodingWebToolError("OLLAMA_API_KEY is required for web tools")
    return key


def _timeout_seconds() -> int:
    try:
        value = int(os.getenv("OPERLY_CODING_WEB_TIMEOUT_SECONDS", "20"))
    except ValueError:
        value = 20
    return max(5, min(value, 60))


def _bounded(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _public_http_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise CodingWebToolError("web_fetch requires an http(s) URL")
    host = parsed.hostname.strip("[]").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise CodingWebToolError("web_fetch accepts public web targets only")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_multicast):
        raise CodingWebToolError("web_fetch accepts public web targets only")
    return value


async def ollama_web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search the public web using Ollama's hosted web-search API."""
    text = _bounded(query, 1000)
    if not text:
        raise CodingWebToolError("web_search query is required")
    count = max(1, min(int(max_results or 5), 10))
    headers = {"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"}
    timeout = aiohttp.ClientTimeout(total=_timeout_seconds())
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://ollama.com/api/web_search",
                headers=headers,
                json={"query": text, "max_results": count},
            ) as response:
                payload = await response.json(content_type=None)
                if response.status >= 400:
                    raise CodingWebToolError(f"Ollama web_search failed ({response.status})")
    except CodingWebToolError:
        raise
    except Exception as error:
        raise CodingWebToolError(f"Ollama web_search unavailable: {error}") from error

    rows = []
    for item in payload.get("results", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "title": _bounded(item.get("title"), 500),
                "url": _bounded(item.get("url"), 2000),
                "content": _bounded(item.get("content"), 6000),
            }
        )
    return {"query": text, "results": rows[:count], "untrustedWebContent": True}


async def ollama_web_fetch(url: str) -> dict[str, Any]:
    """Fetch one public HTTP(S) page through Ollama's hosted web-fetch API."""
    value = _public_http_url(_bounded(url, 3000))
    headers = {"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"}
    timeout = aiohttp.ClientTimeout(total=_timeout_seconds())
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://ollama.com/api/web_fetch",
                headers=headers,
                json={"url": value},
            ) as response:
                payload = await response.json(content_type=None)
                if response.status >= 400:
                    raise CodingWebToolError(f"Ollama web_fetch failed ({response.status})")
    except CodingWebToolError:
        raise
    except Exception as error:
        raise CodingWebToolError(f"Ollama web_fetch unavailable: {error}") from error

    if not isinstance(payload, dict):
        raise CodingWebToolError("Ollama web_fetch returned an invalid response")
    links = [_bounded(item, 2000) for item in payload.get("links", []) if str(item or "").strip()][:100]
    return {
        "url": value,
        "title": _bounded(payload.get("title"), 500),
        "content": _bounded(payload.get("content"), 20_000),
        "links": links,
        "untrustedWebContent": True,
    }
