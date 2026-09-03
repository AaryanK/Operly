from __future__ import annotations

import asyncio
import io
import json
import os
import time
import zipfile
from datetime import datetime, timedelta
from uuid import uuid4

import httpx
from sqlalchemy import select

from apps.api.auth_cookies import PROD_CSRF_COOKIE, PROD_SESSION_COOKIE
from apps.api.security import hash_token, random_token
from packages.database.db import SessionFactory, init_db
from packages.database.models import AppUser, AuthSession, Tenant, TenantMember


PLUGIN_SPECS = (
    ("e2e.lead-pulse", "Lead Pulse", "moderate", 500, 768, "Scores inbound leads and shows a compact sales-priority board."),
    ("e2e.invoice-watch", "Invoice Watch", "moderate", 750, 1024, "Flags invoice exceptions and presents a receivables health snapshot."),
    ("e2e.inventory-forecast", "Inventory Forecast", "heavy", 2000, 4096, "Represents a heavier forecasting workload for inventory planning."),
    ("e2e.support-triage", "Support Triage", "light", 250, 512, "Triage-style support queue with urgency and ownership signals."),
    ("e2e.campaign-board", "Campaign Board", "moderate", 750, 1024, "Organizes campaign work into launch-ready marketing lanes."),
    ("e2e.contract-review", "Contract Review", "heavy", 2500, 4096, "Represents a document-heavy review workload with extracted risk cues."),
    ("e2e.sales-radar", "Sales Radar", "moderate", 1000, 1536, "Shows a compact sales radar for opportunities, velocity, and follow-up."),
    ("e2e.data-reconcile", "Data Reconcile", "heavy", 3000, 6144, "Represents a compute-heavier reconciliation workload across business records."),
)


def _capability_id(plugin_id: str) -> str:
    return f"{plugin_id}.run"


def _manifest(plugin_id: str, name: str, resource_class: str, cpu: int, memory: int, description: str) -> dict:
    capability_id = _capability_id(plugin_id)
    return {
        "schema_version": "operly.plugin/v1",
        "plugin_id": plugin_id,
        "version": "1.0.0",
        "display_name": name,
        "description": description,
        "execution_mode": "sandbox_job",
        "capabilities": [
            {
                "id": capability_id,
                "display_name": f"Run {name}",
                "description": f"Execute the isolated {name} demonstration workload.",
                "input_schema": {
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                    "additionalProperties": False,
                },
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "plugin_id": {"type": "string"},
                        "resource_class": {"type": "string"},
                        "message": {"type": "string"},
                        "runner": {"type": "string"},
                        "invocation_schema": {"type": "string"},
                    },
                    "required": ["plugin_id", "resource_class", "message", "runner", "invocation_schema"],
                    "additionalProperties": False,
                },
                "permissions": [],
                "risk": "read_only",
                "approval_required": False,
                "reversible": False,
                "aliases": [name.lower(), f"{resource_class} plugin"],
                "emits": [],
                "tags": ["e2e", "hosted", resource_class],
            }
        ],
        "permissions": [],
        "configuration_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "runtime": {
            "profile": "sandbox-job",
            "kind": "job",
            "network": {"mode": "off", "allowed_hosts": []},
            "resources": {
                "cpu_millicores": cpu,
                "memory_mb": memory,
                "disk_mb": 2048 if resource_class != "heavy" else 8192,
                "max_runtime_seconds": 300 if resource_class != "heavy" else 900,
                "max_concurrency": 1 if resource_class == "heavy" else 4,
            },
        },
        "storage": [],
        "credentials": [],
        "produces_events": [],
        "consumes_events": [],
        "requested_bindings": [],
        "ui": [{"contribution_type": "navigation", "id": f"{plugin_id}.home", "title": name, "configuration": {"hosted_entry": "index.html"}}],
        "metadata": {
            "source": "operly-api-multiplugin-e2e",
            "resource_class": resource_class,
            "hosted_entry": "index.html",
            "e2e": True,
        },
    }


def _page(name: str, plugin_id: str, resource_class: str, description: str, cpu: int, memory: int) -> str:
    badge = resource_class.upper()
    return f"""<!doctype html>
<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{name} · Operly</title>
<style>
:root{{font-family:Inter,ui-sans-serif,system-ui;color:#f7f4ff;background:#0d0a14}}body{{margin:0;min-height:100vh;background:radial-gradient(circle at 18% 0,#2b1b4c 0,transparent 36%),#0d0a14}}main{{max-width:920px;margin:auto;padding:72px 24px}}.shell{{border:1px solid #3a2a52;background:#171020dd;border-radius:24px;padding:32px;box-shadow:0 24px 80px #0008}}.eyebrow{{font-size:13px;letter-spacing:.16em;text-transform:uppercase;color:#bca6ef}}h1{{font-size:48px;line-height:1;margin:.35em 0}}p{{color:#cfc4e7;line-height:1.7}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-top:28px}}.card{{background:#21172f;border:1px solid #3f3154;border-radius:16px;padding:18px}}.value{{font-size:26px;font-weight:750;margin-top:8px}}code{{color:#d9c8ff}}.ok{{color:#8ce8b4}}footer{{margin-top:28px;font-size:13px;color:#8f82a6}}</style></head>
<body><main><section class='shell'><div class='eyebrow'>Operly Workspace Plugin · {badge}</div><h1>{name}</h1><p>{description}</p>
<div class='grid'><div class='card'>Runtime<div class='value'>Sandbox Job</div></div><div class='card'>Requested CPU<div class='value'>{cpu}m</div></div><div class='card'>Requested memory<div class='value'>{memory} MB</div></div><div class='card'>Status<div class='value ok'>● Active</div></div></div>
<p>This page is being served by the Operly API from the exact artifact that passed isolated validation. Executable capability calls for this plugin run in a fresh Sandbox Runner computer rather than inside the API process.</p>
<footer><code>{plugin_id}</code> · operly.plugin/v1 · workspace-scoped hosted artifact</footer></section></main></body></html>"""


def _package(plugin_id: str, name: str, resource_class: str, cpu: int, memory: int, description: str) -> tuple[dict, bytes]:
    manifest = _manifest(plugin_id, name, resource_class, cpu, memory, description)
    runtime = f'''from __future__ import annotations\nimport json, sys\npacket=json.load(sys.stdin)\narguments=packet.get("arguments") or {{}}\nprint(json.dumps({{"result":{{"plugin_id":{plugin_id!r},"resource_class":{resource_class!r},"message":str(arguments.get("message") or ""),"runner":"operly-sandbox-job","invocation_schema":str(packet.get("schema") or "")}}}},separators=(",",":"),sort_keys=True))\n'''
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("operly.plugin.json", json.dumps(manifest, separators=(",", ":"), sort_keys=True))
        archive.writestr("operly_runtime.py", runtime)
        archive.writestr("index.html", _page(name, plugin_id, resource_class, description, cpu, memory))
    return manifest, buf.getvalue()


async def _bootstrap_session() -> tuple[str, str, str, str]:
    suffix = uuid4().hex[:10]
    now = datetime.utcnow()
    session_secret = random_token()
    csrf_secret = random_token()
    async with SessionFactory() as db:
        tenant = Tenant(name=f"Operly Plugin Lab {suffix}", slug=f"plugin-lab-{suffix}", timezone="UTC")
        user = AppUser(
            email=f"plugin-hosting-e2e-{suffix}@example.com",
            display_name="Operly Plugin Hosting E2E",
            active=True,
            email_verified_at=now,
        )
        db.add_all([tenant, user])
        await db.flush()
        db.add(TenantMember(tenant_id=tenant.id, user_id=user.id, role="owner"))
        db.add(
            AuthSession(
                token_hash=hash_token(session_secret, purpose="session"),
                csrf_token_hash=hash_token(csrf_secret, purpose="csrf"),
                user_id=user.id,
                tenant_id=tenant.id,
                created_at=now,
                expires_at=now + timedelta(hours=4),
                last_activity_at=now,
                authenticated_at=now,
                user_agent="Operly Plugin Hosting E2E",
            )
        )
        await db.commit()
        return tenant.id, tenant.slug or tenant.id, session_secret, csrf_secret


async def _json_response(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError as error:
        raise RuntimeError(f"HTTP {response.status_code} returned non-JSON: {response.text[:1000]}") from error
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {json.dumps(data, sort_keys=True)[:2000]}")
    if not isinstance(data, dict):
        raise RuntimeError("API returned an invalid response shape")
    return data


async def _wait_runtime(client: httpx.AsyncClient, installation_id: str, *, timeout: float = 300.0) -> dict:
    deadline = time.monotonic() + timeout
    last_validation = None
    while time.monotonic() < deadline:
        data = await _json_response(await client.get(f"/api/plugin-platform/installations/{installation_id}/runtime"))
        validation = data.get("validation_status")
        if validation != last_validation:
            print("E2E_VALIDATION", installation_id, validation, flush=True)
            last_validation = validation
        if validation == "failed":
            raise RuntimeError(f"validation failed for {installation_id}")
        healthy = next((item for item in data.get("instances", []) if item.get("state") in {"ready", "running"} and item.get("health_state") == "healthy"), None)
        if healthy:
            return data
        await asyncio.sleep(2)
    raise TimeoutError(f"runtime did not become healthy: {installation_id}")


async def _wait_validation(client: httpx.AsyncClient, installation_id: str, *, timeout: float = 300.0) -> None:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        data = await _json_response(await client.get(f"/api/plugin-platform/installations/{installation_id}/runtime"))
        state = data.get("validation_status")
        if state != last:
            print("E2E_VALIDATION", installation_id, state, flush=True)
            last = state
        if state == "passed":
            return
        if state == "failed":
            raise RuntimeError(f"validation failed: {installation_id}")
        await asyncio.sleep(2)
    raise TimeoutError(f"validation timed out: {installation_id}")


async def main() -> None:
    await init_db()
    base_url = (os.getenv("PUBLIC_BASE_URL") or "https://operly.dragonzpyder.xyz").strip().rstrip("/")
    if not base_url.startswith("https://"):
        raise RuntimeError("PUBLIC_BASE_URL must be HTTPS for the production E2E")
    workspace_id, workspace_slug, session_secret, csrf_secret = await _bootstrap_session()
    print("PLUGIN_HOSTING_E2E_WORKSPACE", workspace_id, workspace_slug, flush=True)
    headers = {
        "Origin": base_url,
        "X-CSRF-Token": csrf_secret,
        "User-Agent": "Operly-Plugin-Hosting-E2E/1",
    }
    cookies = {PROD_SESSION_COOKIE: session_secret, PROD_CSRF_COOKIE: csrf_secret}
    records: list[dict] = []
    async with httpx.AsyncClient(base_url=base_url, headers=headers, cookies=cookies, timeout=120.0, follow_redirects=False) as client:
        health = await _json_response(await client.get("/api/health"))
        print("PLUGIN_HOSTING_E2E_API_HEALTH", health.get("ok"), flush=True)

        # Publish/install all packages through the authenticated Operly API first so
        # the production Worker can validate them concurrently instead of serially.
        for plugin_id, name, resource_class, cpu, memory, description in PLUGIN_SPECS:
            manifest, package_bytes = _package(plugin_id, name, resource_class, cpu, memory, description)
            upload = await _json_response(
                await client.post(
                    "/api/artifacts/upload",
                    files={"files": (f"{plugin_id}.zip", package_bytes, "application/zip")},
                )
            )
            artifact_id = str(upload["artifact_ids"][0])
            published = await _json_response(
                await client.post(
                    "/api/plugin-platform/packages",
                    json={"manifest": manifest, "package_artifact_id": artifact_id},
                )
            )
            installed = await _json_response(
                await client.post(
                    "/api/plugin-platform/installations",
                    json={"version_id": published["version_id"], "granted_permissions": [], "configuration": {}},
                )
            )
            record = {
                "plugin_id": plugin_id,
                "name": name,
                "resource_class": resource_class,
                "cpu_millicores": cpu,
                "memory_mb": memory,
                "artifact_id": artifact_id,
                "version_id": published["version_id"],
                "installation_id": installed["installation_id"],
                "capability_id": _capability_id(plugin_id),
            }
            records.append(record)
            print("PLUGIN_HOSTING_E2E_PUBLISHED", json.dumps(record, sort_keys=True), flush=True)

        await asyncio.gather(*(_wait_validation(client, item["installation_id"]) for item in records))

        for item in records:
            accepted = await _json_response(
                await client.post(
                    f"/api/plugin-platform/installations/{item['installation_id']}/runtime/reconcile",
                    json={},
                )
            )
            item["reconcile_job_id"] = accepted["job_id"]

        runtime_states = await asyncio.gather(*(_wait_runtime(client, item["installation_id"]) for item in records))
        for item, runtime in zip(records, runtime_states):
            instance = next(entry for entry in runtime["instances"] if entry.get("health_state") == "healthy")
            item["runtime_provider"] = instance.get("provider")
            item["runtime_state"] = instance.get("state")
            active = await _json_response(
                await client.patch(
                    f"/api/plugin-platform/installations/{item['installation_id']}",
                    json={"status": "active", "enabled": True},
                )
            )
            item["active"] = bool(active.get("enabled")) and active.get("status") == "active"
            hosted_path = f"/api/public/plugins/{workspace_id}/{item['plugin_id']}"
            hosted = await client.get(hosted_path)
            if hosted.status_code != 200 or item["name"] not in hosted.text:
                raise RuntimeError(f"hosted page failed for {item['plugin_id']}: HTTP {hosted.status_code}")
            item["hosted_url"] = f"{base_url}{hosted_path}"
            item["hosted_status"] = hosted.status_code

        # Execute every installed capability through the authenticated Workspace API.
        # Each call crosses the existing Kernel -> sandbox_job provider -> Sandbox Runner path.
        for item in records:
            execution = await _json_response(
                await client.post(
                    f"/api/workspace-tools/{item['capability_id']}/execute",
                    json={
                        "arguments": {"message": f"hello from {item['name']}"},
                        "goal": f"Verify {item['name']} executes in isolated compute",
                        "request_id": f"hosting-e2e-{uuid4().hex[:16]}",
                    },
                    timeout=240.0,
                )
            )
            result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
            if execution.get("status") != "completed" or result.get("runner") != "operly-sandbox-job":
                raise RuntimeError(f"capability execution failed for {item['plugin_id']}: {json.dumps(execution, sort_keys=True)[:2000]}")
            item["execution_status"] = execution.get("status")
            item["run_id"] = execution.get("run_id")
            item["invocation_schema"] = result.get("invocation_schema")

    report = {
        "ok": True,
        "workspace_id": workspace_id,
        "workspace_slug": workspace_slug,
        "base_url": base_url,
        "plugin_count": len(records),
        "plugins": records,
        "architecture": "Operly API -> durable Platform Worker -> isolated Sandbox Runner -> active Workspace plugin -> Operly public workspace URL",
        "new_railway_services_created": 0,
    }
    print("PLUGIN_HOSTING_E2E_REPORT=" + json.dumps(report, separators=(",", ":"), sort_keys=True), flush=True)
    print("PLUGIN_HOSTING_E2E=PASS", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
