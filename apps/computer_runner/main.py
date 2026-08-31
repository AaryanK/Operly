from __future__ import annotations

import asyncio
import ipaddress
import os
import shutil
import signal
import socket
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

try:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
except Exception:  # pragma: no cover - browser is optional outside the runner image
    Browser = BrowserContext = Page = Playwright = Any  # type: ignore[misc,assignment]
    async_playwright = None


ENVIRONMENT = os.getenv("OPERLY_ENV", os.getenv("APP_ENV", "development")).strip().lower()
DEV_ENABLED = os.getenv("OPERLY_AGENT_COMPUTER_DEV_RUNNER", "").strip().lower() in {"1", "true", "yes", "on"}
RUNNER_TOKEN = os.getenv("OPERLY_AGENT_COMPUTER_RUNNER_TOKEN", "").strip()
ROOT = Path(os.getenv("OPERLY_AGENT_COMPUTER_RUNNER_ROOT", "./.agent-computer-runtime")).resolve()
MAX_STDIO = 2_000_000
MAX_FILE_BYTES = 5_000_000
MAX_DOWNLOAD_BYTES = 50_000_000


@dataclass
class ProcessState:
    id: str
    process: asyncio.subprocess.Process
    command: str
    cwd: str
    log_path: str
    started_at: datetime


@dataclass
class RuntimeSession:
    id: str
    client_session_id: str
    workspace_id: str
    principal_id: str
    root: Path
    profile: str
    network_policy: str
    created_at: datetime
    expires_at: datetime
    state: str = "active"
    processes: dict[str, ProcessState] = field(default_factory=dict)
    playwright: Any | None = None
    browser: Any | None = None
    context: Any | None = None
    page: Any | None = None


SESSIONS: dict[str, RuntimeSession] = {}


class StartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_session_id: str = Field(min_length=1, max_length=80)
    workspace_id: str = Field(min_length=1, max_length=80)
    principal_id: str = Field(min_length=1, max_length=200)
    profile: str = Field(default="general", pattern=r"^(general|coding|data|browser)$")
    ttl_seconds: int = Field(default=7200, ge=60, le=21600)
    network_policy: str = Field(default="web", pattern=r"^(off|web|full)$")


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arguments: dict[str, Any] = Field(default_factory=dict)


app = FastAPI(title="Operly Agent Computer Reference Runner", version="1.0.0")


def _assert_safe_mode() -> None:
    if ENVIRONMENT in {"production", "prod"}:
        raise RuntimeError(
            "The reference Agent Computer runner intentionally refuses production. "
            "Use the same /v1 protocol behind a per-session container or microVM backend."
        )
    if not DEV_ENABLED:
        raise RuntimeError("Set OPERLY_AGENT_COMPUTER_DEV_RUNNER=1 to run the reference Computer runner")
    if not RUNNER_TOKEN:
        raise RuntimeError("OPERLY_AGENT_COMPUTER_RUNNER_TOKEN is required")
    ROOT.mkdir(parents=True, exist_ok=True)


@app.on_event("startup")
async def startup() -> None:
    _assert_safe_mode()


async def authorize(authorization: str | None = Header(default=None)) -> None:
    if not RUNNER_TOKEN or authorization != f"Bearer {RUNNER_TOKEN}":
        raise HTTPException(status_code=401, detail="Runner authentication failed")


def _session(session_id: str) -> RuntimeSession:
    row = SESSIONS.get(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Computer runtime session not found")
    if row.expires_at <= datetime.utcnow():
        row.state = "expired"
        raise HTTPException(status_code=410, detail="Computer runtime session expired")
    if row.state != "active":
        raise HTTPException(status_code=409, detail=f"Computer runtime is {row.state}")
    return row


def _safe_path(row: RuntimeSession, raw: str | None, *, default: str = ".") -> Path:
    value = str(raw or default).strip() or default
    path = (row.root / value.lstrip("/")).resolve()
    if path != row.root and row.root not in path.parents:
        raise HTTPException(status_code=422, detail="Path escaped the Computer workspace")
    return path


def _relative(row: RuntimeSession, path: Path) -> str:
    try:
        value = path.resolve().relative_to(row.root)
    except ValueError:
        return "."
    return str(value) if str(value) != "." else "."


def _bounded(value: bytes, limit: int = MAX_STDIO) -> tuple[str, bool]:
    truncated = len(value) > limit
    return value[:limit].decode("utf-8", "replace"), truncated


def _public_url(raw: str) -> str:
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=422, detail="Only public HTTP(S) URLs are allowed")
    hostname = parsed.hostname.lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise HTTPException(status_code=422, detail="Local/private network targets are blocked")
    try:
        addresses = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise HTTPException(status_code=422, detail="URL hostname could not be resolved") from error
    for item in addresses:
        address = ipaddress.ip_address(item[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise HTTPException(status_code=422, detail="Private/link-local network targets are blocked")
    return raw


def _network_allowed(row: RuntimeSession) -> None:
    if row.network_policy == "off":
        raise HTTPException(status_code=403, detail="Network access is disabled for this Computer session")


async def _run(
    row: RuntimeSession,
    *,
    argv: list[str],
    command_label: str,
    cwd: str | None,
    timeout_seconds: int,
    env: dict[str, str] | None = None,
    background: bool = False,
) -> dict[str, Any]:
    workdir = _safe_path(row, cwd)
    if not workdir.exists() or not workdir.is_dir():
        raise HTTPException(status_code=422, detail="Working directory does not exist")
    clean_env = {
        "PATH": os.getenv("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(row.root),
        "TMPDIR": str(row.root / ".tmp"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "OPERLY_COMPUTER_SESSION_ID": row.id,
    }
    (row.root / ".tmp").mkdir(exist_ok=True)
    for key, value in (env or {}).items():
        if key.startswith("OPERLY_") or key.upper().endswith(("TOKEN", "SECRET", "PASSWORD", "KEY")):
            continue
        clean_env[str(key)[:120]] = str(value)[:8000]

    if background:
        process_id = str(uuid4())
        log_path = row.root / ".processes" / f"{process_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("wb")
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=workdir,
            env=clean_env,
            stdout=log_handle,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        row.processes[process_id] = ProcessState(
            id=process_id,
            process=process,
            command=command_label,
            cwd=_relative(row, workdir),
            log_path=_relative(row, log_path),
            started_at=datetime.utcnow(),
        )
        return {
            "background": True,
            "process_id": process_id,
            "pid": process.pid,
            "log_path": _relative(row, log_path),
        }

    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=workdir,
        env=clean_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        raise HTTPException(status_code=408, detail="Computer command timed out")
    stdout_text, stdout_truncated = _bounded(stdout)
    stderr_text, stderr_truncated = _bounded(stderr)
    return {
        "background": False,
        "exit_code": process.returncode,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }


async def _browser_open(row: RuntimeSession, args: dict[str, Any]) -> dict[str, Any]:
    if async_playwright is None:
        raise HTTPException(status_code=503, detail="Playwright is not installed in this Computer runner")
    if row.page is not None:
        return {"state": "open", "url": row.page.url}
    row.playwright = await async_playwright().start()
    row.browser = await row.playwright.chromium.launch(headless=True)
    width = max(320, min(int(args.get("viewport_width") or 1440), 2560))
    height = max(320, min(int(args.get("viewport_height") or 900), 2000))
    row.context = await row.browser.new_context(viewport={"width": width, "height": height})
    row.page = await row.context.new_page()
    return {"state": "open", "viewport": {"width": width, "height": height}, "url": row.page.url}


async def _browser_close(row: RuntimeSession) -> dict[str, Any]:
    if row.context is not None:
        await row.context.close()
    if row.browser is not None:
        await row.browser.close()
    if row.playwright is not None:
        await row.playwright.stop()
    row.page = row.context = row.browser = row.playwright = None
    return {"state": "closed"}


async def _page(row: RuntimeSession) -> Any:
    if row.page is None:
        await _browser_open(row, {})
    return row.page


def _locator(page: Any, selector: str) -> Any:
    raw = str(selector or "").strip()
    if raw.startswith("text="):
        return page.get_by_text(raw[5:], exact=False).first
    if raw.startswith("role="):
        spec = raw[5:]
        if "[name=" in spec and spec.endswith("]"):
            role, name = spec.split("[name=", 1)
            return page.get_by_role(role, name=name[:-1]).first
        return page.get_by_role(spec).first
    return page.locator(raw).first


async def _tool(row: RuntimeSession, tool_id: str, args: dict[str, Any]) -> dict[str, Any]:
    if tool_id == "terminal.exec":
        command = str(args.get("command") or "")
        if not command:
            raise HTTPException(status_code=422, detail="command is required")
        return await _run(
            row,
            argv=["/bin/bash", "-lc", command],
            command_label=command,
            cwd=args.get("cwd"),
            timeout_seconds=max(1, min(int(args.get("timeout_seconds") or 120), 900)),
            env={str(k): str(v) for k, v in dict(args.get("env") or {}).items()},
            background=bool(args.get("background")),
        )

    if tool_id == "python.exec":
        code = str(args.get("code") or "")
        if not code:
            raise HTTPException(status_code=422, detail="code is required")
        return await _run(
            row,
            argv=["python3", "-c", code],
            command_label="python3 -c <agent-code>",
            cwd=args.get("cwd"),
            timeout_seconds=max(1, min(int(args.get("timeout_seconds") or 120), 900)),
        )

    if tool_id == "files.list":
        target = _safe_path(row, args.get("path"))
        if not target.exists():
            raise HTTPException(status_code=404, detail="Path not found")
        limit = max(1, min(int(args.get("max_entries") or 500), 5000))
        recursive = bool(args.get("recursive"))
        paths = target.rglob("*") if recursive and target.is_dir() else target.iterdir() if target.is_dir() else [target]
        items = []
        for path in paths:
            if len(items) >= limit:
                break
            try:
                stat = path.stat()
            except OSError:
                continue
            items.append({
                "path": _relative(row, path),
                "type": "directory" if path.is_dir() else "file",
                "size_bytes": stat.st_size if path.is_file() else None,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        return {"path": _relative(row, target), "items": items, "truncated": len(items) >= limit}

    if tool_id == "files.read":
        target = _safe_path(row, args.get("path"))
        if not target.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        maximum = max(1, min(int(args.get("max_bytes") or MAX_FILE_BYTES), MAX_FILE_BYTES))
        raw = target.read_bytes()
        content, truncated = _bounded(raw, maximum)
        return {"path": _relative(row, target), "content": content, "size_bytes": len(raw), "truncated": truncated}

    if tool_id == "files.write":
        target = _safe_path(row, args.get("path"))
        content = str(args.get("content") or "").encode("utf-8")
        if len(content) > MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail="File write exceeds runner limit")
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = "ab" if bool(args.get("append")) else "wb"
        with target.open(mode) as handle:
            handle.write(content)
        return {"path": _relative(row, target), "size_bytes": target.stat().st_size}

    if tool_id == "files.mkdir":
        target = _safe_path(row, args.get("path"))
        target.mkdir(parents=True, exist_ok=True)
        return {"path": _relative(row, target), "created": True}

    if tool_id == "files.remove":
        target = _safe_path(row, args.get("path"))
        if target == row.root:
            raise HTTPException(status_code=422, detail="Cannot remove the Computer workspace root")
        if not target.exists():
            return {"path": _relative(row, target), "removed": False, "missing": True}
        if target.is_dir():
            if not bool(args.get("recursive")) and any(target.iterdir()):
                raise HTTPException(status_code=409, detail="Directory is not empty; recursive=true is required")
            shutil.rmtree(target)
        else:
            target.unlink()
        return {"path": _relative(row, target), "removed": True}

    if tool_id == "files.move":
        source = _safe_path(row, args.get("source"))
        destination = _safe_path(row, args.get("destination"))
        if not source.exists():
            raise HTTPException(status_code=404, detail="Source path not found")
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)
        return {"source": _relative(row, source), "destination": _relative(row, destination), "moved": True}

    if tool_id == "files.search":
        target = _safe_path(row, args.get("path"))
        query = str(args.get("query") or "")
        glob = str(args.get("glob") or "*")
        limit = max(1, min(int(args.get("max_matches") or 200), 1000))
        matches: list[dict[str, Any]] = []
        candidates = target.rglob(glob) if target.is_dir() else [target]
        for path in candidates:
            if len(matches) >= limit:
                break
            if not path.is_file():
                continue
            if query.lower() in path.name.lower():
                matches.append({"path": _relative(row, path), "kind": "filename"})
                if len(matches) >= limit:
                    break
            try:
                if path.stat().st_size > 2_000_000:
                    continue
                text = path.read_text("utf-8", errors="replace")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if query.lower() in line.lower():
                    matches.append({"path": _relative(row, path), "kind": "content", "line": number, "text": line[:1000]})
                    if len(matches) >= limit:
                        break
        return {"query": query, "matches": matches, "truncated": len(matches) >= limit}

    if tool_id == "process.list":
        items = []
        for process_id, item in list(row.processes.items()):
            returncode = item.process.returncode
            items.append({
                "process_id": process_id,
                "pid": item.process.pid,
                "command": item.command,
                "cwd": item.cwd,
                "log_path": item.log_path,
                "state": "running" if returncode is None else "exited",
                "exit_code": returncode,
                "started_at": item.started_at.isoformat(),
            })
        return {"processes": items}

    if tool_id == "process.kill":
        process_id = str(args.get("process_id") or "")
        item = row.processes.get(process_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Background process not found")
        sig = {"TERM": signal.SIGTERM, "KILL": signal.SIGKILL, "INT": signal.SIGINT}.get(str(args.get("signal") or "TERM"), signal.SIGTERM)
        if item.process.returncode is None:
            try:
                os.killpg(item.process.pid, sig)
            except ProcessLookupError:
                pass
            await item.process.wait()
        return {"process_id": process_id, "stopped": True, "exit_code": item.process.returncode}

    if tool_id in {"git.status", "git.diff", "git.exec"}:
        if tool_id == "git.status":
            git_args = ["status", "--short", "--branch"]
        elif tool_id == "git.diff":
            git_args = ["diff"]
            if bool(args.get("staged")):
                git_args.append("--cached")
            if args.get("path"):
                git_args.extend(["--", str(args["path"])])
        else:
            git_args = [str(value) for value in list(args.get("args") or [])]
            if not git_args:
                raise HTTPException(status_code=422, detail="git args are required")
            allowed = {
                "status", "diff", "log", "show", "branch", "checkout", "switch", "restore",
                "add", "commit", "init", "clone", "fetch", "pull", "rev-parse", "ls-files",
            }
            if git_args[0] not in allowed:
                raise HTTPException(status_code=403, detail=f"git subcommand is not enabled: {git_args[0]}")
        return await _run(
            row,
            argv=["git", *git_args],
            command_label="git " + " ".join(git_args),
            cwd=args.get("cwd"),
            timeout_seconds=max(1, min(int(args.get("timeout_seconds") or 120), 900)),
        )

    if tool_id == "web.fetch":
        _network_allowed(row)
        url = _public_url(str(args.get("url") or ""))
        maximum = max(1, min(int(args.get("max_bytes") or 2_000_000), 5_000_000))
        method = str(args.get("method") or "GET")
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.request(method, url, headers={"User-Agent": "Operly-Agent-Computer/1"})
        raw = response.content
        content, truncated = _bounded(raw, maximum)
        return {
            "url": str(response.url),
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type"),
            "content": content if method != "HEAD" else "",
            "size_bytes": len(raw),
            "truncated": truncated,
        }

    if tool_id == "web.download":
        _network_allowed(row)
        url = _public_url(str(args.get("url") or ""))
        destination = _safe_path(row, args.get("destination"))
        maximum = max(1, min(int(args.get("max_bytes") or MAX_DOWNLOAD_BYTES), MAX_DOWNLOAD_BYTES))
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "Operly-Agent-Computer/1"})
            response.raise_for_status()
            raw = response.content
        if len(raw) > maximum:
            raise HTTPException(status_code=413, detail="Download exceeds Computer runner limit")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        return {"url": str(response.url), "path": _relative(row, destination), "size_bytes": len(raw)}

    if tool_id == "browser.open":
        _network_allowed(row)
        return await _browser_open(row, args)

    if tool_id == "browser.close":
        return await _browser_close(row)

    if tool_id == "browser.navigate":
        _network_allowed(row)
        page = await _page(row)
        url = _public_url(str(args.get("url") or ""))
        timeout_ms = max(1000, min(int(args.get("timeout_seconds") or 60), 900)) * 1000
        response = await page.goto(url, wait_until=str(args.get("wait_until") or "domcontentloaded"), timeout=timeout_ms)
        return {"url": page.url, "title": await page.title(), "status_code": response.status if response else None}

    if tool_id == "browser.snapshot":
        page = await _page(row)
        maximum = max(1000, min(int(args.get("max_chars") or 40000), 100000))
        data = await page.evaluate(
            """() => {
              const clean = s => String(s || '').replace(/\\s+/g,' ').trim();
              const items = [...document.querySelectorAll('a,button,input,textarea,select,[role]')].slice(0,500).map((el,i) => ({
                index:i, tag:el.tagName.toLowerCase(), role:el.getAttribute('role') || '',
                text:clean(el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').slice(0,300),
                id:el.id || '', name:el.getAttribute('name') || '', href:el.getAttribute('href') || ''
              }));
              return {title:document.title,url:location.href,text:clean(document.body?.innerText || ''),interactive:items};
            }"""
        )
        data["text"] = str(data.get("text") or "")[:maximum]
        data["truncated"] = len(str(data.get("text") or "")) >= maximum
        return data

    if tool_id == "browser.click":
        page = await _page(row)
        locator = _locator(page, str(args.get("selector") or ""))
        await locator.click(timeout=max(1, min(int(args.get("timeout_seconds") or 30), 900)) * 1000)
        return {"url": page.url, "title": await page.title(), "clicked": True}

    if tool_id == "browser.type":
        page = await _page(row)
        locator = _locator(page, str(args.get("selector") or ""))
        await locator.fill(str(args.get("text") or ""), timeout=max(1, min(int(args.get("timeout_seconds") or 30), 900)) * 1000)
        if bool(args.get("press_enter")):
            await locator.press("Enter")
        return {"url": page.url, "typed": True}

    if tool_id == "browser.press":
        page = await _page(row)
        key = str(args.get("key") or "")
        if args.get("selector"):
            await _locator(page, str(args["selector"])).press(key)
        else:
            await page.keyboard.press(key)
        return {"url": page.url, "pressed": key}

    if tool_id == "browser.evaluate":
        page = await _page(row)
        result = await page.evaluate(str(args.get("expression") or ""))
        return {"url": page.url, "value": result}

    if tool_id == "browser.screenshot":
        page = await _page(row)
        target = _safe_path(row, args.get("path") or "screenshots/page.png")
        if target.suffix.lower() != ".png":
            target = target.with_suffix(".png")
        target.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(target), full_page=bool(args.get("full_page", True)))
        return {"url": page.url, "path": _relative(row, target), "size_bytes": target.stat().st_size}

    raise HTTPException(status_code=404, detail=f"Unknown Computer tool: {tool_id}")


TOOL_IDS = [
    "terminal.exec", "python.exec",
    "files.list", "files.read", "files.write", "files.mkdir", "files.remove", "files.move", "files.search",
    "process.list", "process.kill",
    "git.status", "git.diff", "git.exec",
    "web.fetch", "web.download",
    "browser.open", "browser.navigate", "browser.snapshot", "browser.click", "browser.type", "browser.press", "browser.evaluate", "browser.screenshot", "browser.close",
]


@app.get("/v1/health", dependencies=[Depends(authorize)])
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "runner": "operly-reference-computer-runner",
        "environment": ENVIRONMENT,
        "production_safe": False,
        "isolation": "separate-development-service",
        "python": shutil.which("python3") is not None,
        "bash": shutil.which("bash") is not None,
        "git": shutil.which("git") is not None,
        "browser": async_playwright is not None,
        "tools": TOOL_IDS,
    }


@app.post("/v1/sessions", dependencies=[Depends(authorize)])
async def start_session(payload: StartInput) -> dict[str, Any]:
    session_id = str(uuid4())
    root = (ROOT / session_id).resolve()
    if ROOT not in root.parents:
        raise HTTPException(status_code=500, detail="Invalid runtime root")
    root.mkdir(parents=True, exist_ok=False)
    now = datetime.utcnow()
    row = RuntimeSession(
        id=session_id,
        client_session_id=payload.client_session_id,
        workspace_id=payload.workspace_id,
        principal_id=payload.principal_id,
        root=root,
        profile=payload.profile,
        network_policy=payload.network_policy,
        created_at=now,
        expires_at=now + timedelta(seconds=payload.ttl_seconds),
    )
    SESSIONS[session_id] = row
    return {
        "session_id": session_id,
        "state": "active",
        "profile": row.profile,
        "network_policy": row.network_policy,
        "workspace": ".",
        "expires_at": row.expires_at.isoformat(),
        "tools": TOOL_IDS,
    }


@app.get("/v1/sessions/{session_id}", dependencies=[Depends(authorize)])
async def session_status(session_id: str) -> dict[str, Any]:
    row = _session(session_id)
    return {
        "session_id": row.id,
        "state": row.state,
        "profile": row.profile,
        "network_policy": row.network_policy,
        "expires_at": row.expires_at.isoformat(),
        "process_count": sum(1 for item in row.processes.values() if item.process.returncode is None),
        "browser_open": row.page is not None,
        "tools": TOOL_IDS,
    }


@app.delete("/v1/sessions/{session_id}", dependencies=[Depends(authorize)])
async def stop_session(session_id: str) -> dict[str, Any]:
    row = SESSIONS.get(session_id)
    if row is None:
        return {"session_id": session_id, "state": "stopped", "already_stopped": True}
    for item in row.processes.values():
        if item.process.returncode is None:
            try:
                os.killpg(item.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await item.process.wait()
    await _browser_close(row)
    row.state = "stopped"
    shutil.rmtree(row.root, ignore_errors=True)
    SESSIONS.pop(session_id, None)
    return {"session_id": session_id, "state": "stopped"}


@app.post("/v1/sessions/{session_id}/tools/{tool_id:path}", dependencies=[Depends(authorize)])
async def execute_tool(session_id: str, tool_id: str, payload: ToolInput) -> dict[str, Any]:
    row = _session(session_id)
    return {**(await _tool(row, tool_id, payload.arguments)), "runtime_state": row.state}
