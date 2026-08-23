"""Minimal sidecar used by the production runner.

Modes:
- ``egress``: HTTP CONNECT proxy that only reaches an explicit registry allowlist.
- ``preview``: dumb TCP forwarder from the runner control network to one job runtime.

The sidecar deliberately has no Docker socket, runner token, Operly credentials, or
business-service bindings. It is the only component allowed to bridge the isolated
job network to another network.
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from urllib.parse import urlsplit

_LISTEN_HOST = os.getenv("OPERLY_PROXY_HOST", "0.0.0.0")
_LISTEN_PORT = int(os.getenv("OPERLY_PROXY_PORT", "8081"))
_MODE = os.getenv("OPERLY_PROXY_MODE", "egress").strip().lower()
_ALLOWED = {
    item.strip().lower().rstrip(".")
    for item in os.getenv(
        "OPERLY_PROXY_ALLOW_HOSTS",
        "pypi.org,files.pythonhosted.org,registry.npmjs.org",
    ).split(",")
    if item.strip()
}
_TARGET_HOST = os.getenv("OPERLY_PROXY_TARGET_HOST", "").strip()
_TARGET_PORT = int(os.getenv("OPERLY_PROXY_TARGET_PORT", "8080"))
_MAX_HEADER_BYTES = 32 * 1024


def _safe_hostname(value: str) -> str:
    host = value.strip().lower().rstrip(".")
    if not host or len(host) > 253 or "/" in host or "@" in host or " " in host:
        raise ValueError("invalid host")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("IP literals are not accepted by the registry proxy")
    if host not in _ALLOWED:
        raise ValueError("host is not allowlisted")
    return host


def _global_addresses(host: str, port: int) -> list[str]:
    rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    resolved: list[str] = []
    for row in rows:
        address = row[4][0]
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            continue
        if address not in resolved:
            resolved.append(address)
    if not resolved:
        raise ValueError("registry host did not resolve to a public address")
    return resolved


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(64 * 1024)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.write_eof()
        except (AttributeError, OSError, RuntimeError):
            pass


async def _tunnel(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    upstream_reader: asyncio.StreamReader,
    upstream_writer: asyncio.StreamWriter,
) -> None:
    await asyncio.gather(
        _pipe(client_reader, upstream_writer),
        _pipe(upstream_reader, client_writer),
        return_exceptions=True,
    )
    upstream_writer.close()
    client_writer.close()
    await asyncio.gather(
        upstream_writer.wait_closed(),
        client_writer.wait_closed(),
        return_exceptions=True,
    )


async def _preview(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
) -> None:
    if not _TARGET_HOST:
        client_writer.close()
        await client_writer.wait_closed()
        return
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(
            _TARGET_HOST, _TARGET_PORT
        )
    except OSError:
        client_writer.close()
        await client_writer.wait_closed()
        return
    await _tunnel(client_reader, client_writer, upstream_reader, upstream_writer)


async def _connect_proxy(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
) -> None:
    try:
        request_line = await client_reader.readline()
        if not request_line or len(request_line) > 4096:
            raise ValueError("invalid proxy request")
        try:
            method, target, _version = request_line.decode("ascii").strip().split(" ", 2)
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError("invalid proxy request line") from error

        total = len(request_line)
        while True:
            line = await client_reader.readline()
            total += len(line)
            if total > _MAX_HEADER_BYTES:
                raise ValueError("proxy headers too large")
            if line in {b"\r\n", b"\n", b""}:
                break

        if method.upper() != "CONNECT":
            # pip/npm registry traffic is HTTPS. Refusing ordinary forwarding keeps
            # this proxy from becoming a generic HTTP egress path.
            client_writer.write(
                b"HTTP/1.1 405 Method Not Allowed\r\nConnection: close\r\n\r\n"
            )
            await client_writer.drain()
            return

        parsed = urlsplit("//" + target)
        host = _safe_hostname(parsed.hostname or "")
        port = parsed.port or 443
        if port != 443:
            raise ValueError("registry CONNECT is restricted to TLS port 443")
        addresses = await asyncio.to_thread(_global_addresses, host, port)

        upstream_reader = upstream_writer = None
        last_error: Exception | None = None
        for address in addresses:
            try:
                upstream_reader, upstream_writer = await asyncio.open_connection(
                    address, port
                )
                break
            except OSError as error:
                last_error = error
        if upstream_reader is None or upstream_writer is None:
            raise OSError("unable to reach registry") from last_error

        client_writer.write(
            b"HTTP/1.1 200 Connection Established\r\nProxy-Agent: operly-runner\r\n\r\n"
        )
        await client_writer.drain()
        await _tunnel(
            client_reader,
            client_writer,
            upstream_reader,
            upstream_writer,
        )
        return
    except (ValueError, OSError):
        try:
            client_writer.write(
                b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n"
            )
            await client_writer.drain()
        except OSError:
            pass
    finally:
        if not client_writer.is_closing():
            client_writer.close()
            await client_writer.wait_closed()


async def _handle(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    if _MODE == "preview":
        await _preview(reader, writer)
        return
    await _connect_proxy(reader, writer)


async def main() -> None:
    if _MODE not in {"egress", "preview"}:
        raise RuntimeError("OPERLY_PROXY_MODE must be egress or preview")
    if _MODE == "preview" and not _TARGET_HOST:
        raise RuntimeError("preview mode requires OPERLY_PROXY_TARGET_HOST")
    server = await asyncio.start_server(_handle, _LISTEN_HOST, _LISTEN_PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
