from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlencode


class PublicHostPolicyError(PermissionError):
    pass


class PublicHostResolutionError(RuntimeError):
    pass


def public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


async def resolve_public_addresses(host: str, port: int = 443) -> tuple[str, ...]:
    """Resolve a public hostname once and return only addresses safe to connect to.

    Callers must use one of the returned literal IPs for the socket connection rather
    than resolving ``host`` again. The original hostname is still used as TLS SNI and
    the HTTP Host header. This closes the validation/request DNS-rebinding gap.
    """

    clean = str(host or "").strip().lower().rstrip(".")
    if not clean or clean in {"localhost", "localhost.localdomain"} or clean.endswith(
        (".localhost", ".local", ".internal", ".home.arpa")
    ):
        raise PublicHostPolicyError("Target host is local or invalid")
    try:
        rows = await asyncio.to_thread(
            socket.getaddrinfo,
            clean,
            int(port),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as error:
        raise PublicHostResolutionError("Target host DNS could not be resolved") from error
    addresses = tuple(sorted({str(row[4][0]) for row in rows if row and row[4]}))
    if not addresses:
        raise PublicHostResolutionError("Target host DNS returned no addresses")
    if any(not public_address(address) for address in addresses):
        raise PublicHostPolicyError(
            "Target host resolves to a private, local, or reserved address"
        )
    return addresses


def pinned_https_url(
    address: str,
    *,
    port: int = 443,
    path: str = "/",
    query: str | dict[str, str] | None = None,
) -> str:
    ip = ipaddress.ip_address(address)
    authority = f"[{ip.compressed}]" if ip.version == 6 else ip.compressed
    if int(port) != 443:
        authority = f"{authority}:{int(port)}"
    clean_path = str(path or "/")
    if not clean_path.startswith("/"):
        clean_path = "/" + clean_path
    if isinstance(query, dict):
        query_text = urlencode(query)
    else:
        query_text = str(query or "")
    return f"https://{authority}{clean_path}" + (f"?{query_text}" if query_text else "")


def host_header(host: str, port: int = 443) -> str:
    clean = str(host or "").strip().lower().rstrip(".")
    return clean if int(port) == 443 else f"{clean}:{int(port)}"


def sni_extensions(host: str) -> dict[str, str]:
    # httpcore honors this request extension when negotiating TLS, letting httpx
    # connect to a validated literal IP while verifying the certificate for ``host``.
    return {"sni_hostname": str(host or "").strip().lower().rstrip(".")}
