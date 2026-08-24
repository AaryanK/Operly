"""Minimal sidecar used by the production runner.

Modes:
- ``egress``: HTTP CONNECT proxy that only reaches an explicit registry allowlist.
- ``preview``: dumb TCP forwarder from the runner control network to one job runtime.
- ``binding``: fixed-destination HTTP gateway that injects one scoped capability grant.

Generated code never receives the sidecar's token, Docker socket, runner token, Operly
session, provider credential, or direct route to the sidecar's external network.
"""
from __future__ import annotations

import asyncio
import http.client
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
_BINDING_TARGET = os.getenv("OPERLY_PROXY_BINDING_TARGET", "").strip().rstrip("/")
_BINDING_TOKEN = os.getenv("OPERLY_PROXY_BINDING_TOKEN", "")
_BINDING_PREFIX = os.getenv(
    "OPERLY_PROXY_BINDING_PREFIX", "/api/runtime/relational"
).rstrip("/")
_MAX_HEADER_BYTES = 32 * 1024
_MAX_BINDING_BODY = 1024 * 1024


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


def _binding_request(method: str, path: str, body: bytes, content_type: str) -> tuple[int, str, bytes]:
    target = urlsplit(_BINDING_TARGET)
    if target.scheme == "https":
        connection = http.client.HTTPSConnection(target.hostname, target.port or 443, timeout=8)
    elif target.scheme == "http":
        connection = http.client.HTTPConnection(target.hostname, target.port or 80, timeout=8)
    else:
        raise ValueError("binding target scheme is invalid")
    base = (target.path or "").rstrip("/")
    forwarded_path = base + _BINDING_PREFIX + (path if path.startswith("/") else "/" + path)
    headers = {
        "Authorization": f"Bearer {_BINDING_TOKEN}",
        "Content-Type": content_type or "application/json",
        "Accept": "application/json",
        "Connection": "close",
    }
    try:
        connection.request(method, forwarded_path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read(_MAX_BINDING_BODY + 1)
        if len(payload) > _MAX_BINDING_BODY:
            raise ValueError("binding response too large")
        return response.status, response.getheader("Content-Type") or "application/json", payload
    finally:
        connection.close()


async def _binding(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        request_line = await reader.readline()
        if not request_line or len(request_line) > 4096:
            raise ValueError("invalid binding request")
        try:
            method, path, version = request_line.decode("ascii").strip().split(" ", 2)
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError("invalid binding request line") from error
        method = method.upper()
        if method not in {"GET", "POST"} or not path.startswith("/") or "//" in path:
            raise ValueError("binding request method/path is forbidden")
        if version not in {"HTTP/1.0", "HTTP/1.1"}:
            raise ValueError("binding request version is invalid")

        headers: dict[str, str] = {}
        total = len(request_line)
        while True:
            line = await reader.readline()
            total += len(line)
            if total > _MAX_HEADER_BYTES:
                raise ValueError("binding headers too large")
            if line in {b"\r\n", b"\n", b""}:
                break
            try:
                key, value = line.decode("latin1").split(":", 1)
            except ValueError as error:
                raise ValueError("invalid binding header") from error
            headers[key.strip().lower()] = value.strip()
        length = int(headers.get("content-length", "0") or 0)
        if length < 0 or length > _MAX_BINDING_BODY:
            raise ValueError("binding body too large")
        body = await reader.readexactly(length) if length else b""
        status, content_type, payload = await asyncio.to_thread(
            _binding_request,
            method,
            path,
            body,
            headers.get("content-type", "application/json"),
        )
        reason = http.client.responses.get(status, "Response")
        writer.write(
            f"HTTP/1.1 {status} {reason}\r\nContent-Type: {content_type}\r\nContent-Length: {len(payload)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n".encode("latin1")
            + payload
        )
        await writer.drain()
    except Exception:
        payload = b'{"detail":"capability_binding_proxy_failure"}'
        try:
            writer.write(
                b"HTTP/1.1 502 Bad Gateway\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                + payload
            )
            await writer.drain()
        except OSError:
            pass
    finally:
        writer.close()
        await asyncio.gather(writer.wait_closed(), return_exceptions=True)


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    if _MODE == "preview":
        await _preview(reader, writer)
        return
    if _MODE == "binding":
        await _binding(reader, writer)
        return
    await _connect_proxy(reader, writer)


async def main() -> None:
    if _MODE not in {"egress", "preview", "binding"}:
        raise RuntimeError("OPERLY_PROXY_MODE must be egress, preview, or binding")
    if _MODE == "preview" and not _TARGET_HOST:
        raise RuntimeError("preview mode requires OPERLY_PROXY_TARGET_HOST")
    if _MODE == "binding":
        parsed = urlsplit(_BINDING_TARGET)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise RuntimeError("binding mode requires a valid OPERLY_PROXY_BINDING_TARGET")
        if len(_BINDING_TOKEN) < 40:
            raise RuntimeError("binding mode requires a scoped binding token")
        if not _BINDING_PREFIX.startswith("/"):
            raise RuntimeError("binding prefix must be an absolute path")
    server = await asyncio.start_server(_handle, _LISTEN_HOST, _LISTEN_PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
