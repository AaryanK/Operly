from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import secrets
import time
from datetime import datetime

import httpx

from apps.api.auth_cookies import PROD_CSRF_COOKIE, PROD_SESSION_COOKIE
from packages.database.db import init_db
from packages.plugins.temp_app_suite import (
    APP_SPECS,
    _bootstrap_identity,
    _capability_id,
    _json,
    _package,
    _wait_runtime,
    _wait_validation,
)

WORKSPACE_COUNT = 4
SELECTED_IDS = {
    "temp.support-desk",
    "temp.inventory-planner",
    "temp.contract-review",
    "temp.cash-forecast",
}
SELECTED_SPECS = [spec for spec in APP_SPECS if spec["id"] in SELECTED_IDS]


def _workspace_state(spec: dict, label: str) -> dict:
    records = copy.deepcopy(spec["seed"])
    return {
        "records": records,
        "workspace_marker": label,
        "updated_at": datetime.utcnow().isoformat(),
    }


async def _prepare_workspace(index: int, base_url: str) -> dict:
    workspace_id, workspace_slug, session_secret, csrf_secret = await _bootstrap_identity()
    label = f"tenant-{index + 1}-{workspace_slug[-6:]}"
    demo_token = secrets.token_urlsafe(32)
    demo_token_hash = hashlib.sha256(demo_token.encode("utf-8")).hexdigest()
    headers = {
        "Origin": base_url,
        "X-CSRF-Token": csrf_secret,
        "User-Agent": f"Operly-Temp-Multiworkspace/{index + 1}",
    }
    cookies = {PROD_SESSION_COOKIE: session_secret, PROD_CSRF_COOKIE: csrf_secret}
    records: list[dict] = []

    print("TEMP_MULTI_WORKSPACE_START", index + 1, workspace_id, workspace_slug, flush=True)
    async with httpx.AsyncClient(base_url=base_url, headers=headers, cookies=cookies, timeout=120.0) as client:
        health = _json(await client.get("/api/health"))
        if not health.get("ok"):
            raise RuntimeError(f"Operly API unhealthy for {label}")

        for spec in SELECTED_SPECS:
            manifest, package_bytes = _package(spec)
            upload = _json(
                await client.post(
                    "/api/artifacts/upload",
                    files={"files": (f"{spec['id']}.zip", package_bytes, "application/zip")},
                )
            )
            published = _json(
                await client.post(
                    "/api/plugin-platform/packages",
                    json={"manifest": manifest, "package_artifact_id": upload["artifact_ids"][0]},
                )
            )
            seed_state = _workspace_state(spec, label)
            installed = _json(
                await client.post(
                    "/api/plugin-platform/installations",
                    json={
                        "version_id": published["version_id"],
                        "granted_permissions": [],
                        "configuration": {
                            "temporary_demo": True,
                            "multiworkspace_test": True,
                            "workspace_marker": label,
                            "demo_token_hash": demo_token_hash,
                            "demo_name": spec["name"],
                            "demo_category": spec["category"],
                            "demo_description": spec["description"],
                            "demo_seed": seed_state,
                        },
                    },
                )
            )
            records.append(
                {
                    "plugin_id": spec["id"],
                    "name": spec["name"],
                    "installation_id": installed["installation_id"],
                    "capability_id": _capability_id(spec["id"]),
                }
            )

        await asyncio.gather(*(_wait_validation(client, item["installation_id"]) for item in records))

        for item in records:
            accepted = _json(
                await client.post(
                    f"/api/plugin-platform/installations/{item['installation_id']}/runtime/reconcile",
                    json={},
                )
            )
            item["reconcile_job_id"] = accepted["job_id"]

        instances = await asyncio.gather(*(_wait_runtime(client, item["installation_id"]) for item in records))
        for item, instance in zip(records, instances):
            item["runtime_provider"] = instance.get("provider")
            active = _json(
                await client.patch(
                    f"/api/plugin-platform/installations/{item['installation_id']}",
                    json={"status": "active", "enabled": True},
                )
            )
            if not active.get("enabled"):
                raise RuntimeError(f"Activation failed for {label}/{item['plugin_id']}")

        demo_headers = {"X-Operly-Demo-Token": demo_token, "Origin": base_url}
        for item in records:
            spec = next(spec for spec in SELECTED_SPECS if spec["id"] == item["plugin_id"])
            state = _workspace_state(spec, label)
            _json(
                await client.put(
                    f"/api/public/plugin-demos/{workspace_id}/{item['plugin_id']}/state",
                    headers=demo_headers,
                    json={"state": state},
                )
            )
            own = _json(
                await client.get(
                    f"/api/public/plugin-demos/{workspace_id}/{item['plugin_id']}/state",
                    headers=demo_headers,
                )
            )
            own_state = own.get("state", own)
            if own_state.get("workspace_marker") != label:
                raise RuntimeError(f"State marker mismatch for {label}/{item['plugin_id']}")
            hosted = await client.get(f"/api/public/plugins/{workspace_id}/{item['plugin_id']}/")
            if hosted.status_code != 200 or spec["name"] not in hosted.text:
                raise RuntimeError(f"Hosted UI failed for {label}/{item['plugin_id']}: HTTP {hosted.status_code}")
            item["hosted_url"] = f"{base_url}/api/public/plugins/{workspace_id}/{item['plugin_id']}/"

    return {
        "index": index,
        "label": label,
        "workspace_id": workspace_id,
        "workspace_slug": workspace_slug,
        "demo_token": demo_token,
        "apps": records,
    }


async def _execute_app(base_url: str, workspace: dict, item: dict) -> dict:
    headers = {
        "X-Operly-Demo-Token": workspace["demo_token"],
        "Origin": base_url,
    }
    async with httpx.AsyncClient(base_url=base_url, timeout=180.0) as client:
        execution = _json(
            await client.post(
                f"/api/public/plugin-demos/{workspace['workspace_id']}/{item['plugin_id']}/execute",
                headers=headers,
                json={"action": "analyze"},
            )
        )
    result = execution.get("result", {})
    if not result.get("summary"):
        raise RuntimeError(f"Sandbox analysis failed for {workspace['label']}/{item['plugin_id']}")
    return {
        "workspace_id": workspace["workspace_id"],
        "plugin_id": item["plugin_id"],
        "run_id": execution.get("run_id"),
        "summary": result.get("summary"),
    }


async def _verify_cross_tenant_isolation(base_url: str, workspaces: list[dict]) -> list[dict]:
    checks: list[dict] = []
    async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as client:
        for index, source in enumerate(workspaces):
            target = workspaces[(index + 1) % len(workspaces)]
            plugin_id = target["apps"][0]["plugin_id"]
            response = await client.get(
                f"/api/public/plugin-demos/{target['workspace_id']}/{plugin_id}/state",
                headers={
                    "X-Operly-Demo-Token": source["demo_token"],
                    "Origin": base_url,
                },
            )
            if response.status_code < 400:
                raise RuntimeError(
                    f"Cross-tenant token unexpectedly accessed state: {source['label']} -> {target['label']}"
                )
            checks.append(
                {
                    "from": source["workspace_id"],
                    "to": target["workspace_id"],
                    "plugin_id": plugin_id,
                    "status": response.status_code,
                }
            )
    return checks


async def main() -> None:
    await init_db()
    base_url = (os.getenv("PUBLIC_BASE_URL") or "https://operly.dragonzpyder.xyz").strip().rstrip("/")
    started = time.monotonic()

    workspaces = await asyncio.gather(
        *(_prepare_workspace(index, base_url) for index in range(WORKSPACE_COUNT))
    )
    print("TEMP_MULTI_ALL_WORKSPACES_READY", len(workspaces), flush=True)

    executions = await asyncio.gather(
        *(
            _execute_app(base_url, workspace, item)
            for workspace in workspaces
            for item in workspace["apps"]
        )
    )
    isolation = await _verify_cross_tenant_isolation(base_url, workspaces)

    public_workspaces = []
    for workspace in workspaces:
        public_workspaces.append(
            {
                "label": workspace["label"],
                "workspace_id": workspace["workspace_id"],
                "workspace_slug": workspace["workspace_slug"],
                "lab_url": f"{base_url}/temp-app-lab/{workspace['workspace_id']}?token={workspace['demo_token']}",
                "apps": workspace["apps"],
            }
        )

    result = {
        "status": "PASS",
        "workspace_count": len(workspaces),
        "apps_per_workspace": len(SELECTED_SPECS),
        "total_installations": sum(len(workspace["apps"]) for workspace in workspaces),
        "concurrent_execution_count": len(executions),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "isolation_checks": isolation,
        "executions": executions,
        "workspaces": public_workspaces,
    }
    print("TEMP_MULTI_RESULT", json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
