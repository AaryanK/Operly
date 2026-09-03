from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx

from packages.plugins import temp_app_suite as suite


WORKSPACE_PLANS = [
    {
        "label": "Retail Ops",
        "slug": "retail",
        "plugins": ["temp.inventory-planner", "temp.fulfillment-board", "temp.procurement-hub"],
    },
    {
        "label": "Revenue Team",
        "slug": "revenue",
        "plugins": ["temp.lead-pipeline", "temp.campaign-planner", "temp.customer-health"],
    },
    {
        "label": "Finance Team",
        "slug": "finance",
        "plugins": ["temp.receivables", "temp.cash-forecast", "temp.contract-review"],
    },
    {
        "label": "Service Ops",
        "slug": "service",
        "plugins": ["temp.support-desk", "temp.ops-board", "temp.inventory-planner"],
    },
]

SPEC_BY_ID = {spec["id"]: spec for spec in suite.APP_SPECS}


def _utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _bootstrap_workspace(label: str, slug_prefix: str) -> tuple[str, str, str, str]:
    suffix = uuid4().hex[:10]
    now = _utc_naive()
    session_secret = suite.random_token()
    csrf_secret = suite.random_token()
    async with suite.SessionFactory() as db:
        tenant = suite.Tenant(
            name=f"TEMP Multi Workspace — {label} — {suffix}",
            slug=f"temp-multi-{slug_prefix}-{suffix}",
            timezone="UTC",
        )
        user = suite.AppUser(
            email=f"temp-multi-{slug_prefix}-{suffix}@example.com",
            display_name=f"Temporary {label} Owner",
            active=True,
            email_verified_at=now,
        )
        db.add_all([tenant, user])
        await db.flush()
        db.add(suite.TenantMember(tenant_id=tenant.id, user_id=user.id, role="owner"))
        db.add(
            suite.AuthSession(
                token_hash=suite.hash_token(session_secret, purpose="session"),
                csrf_token_hash=suite.hash_token(csrf_secret, purpose="csrf"),
                user_id=user.id,
                tenant_id=tenant.id,
                created_at=now,
                expires_at=now + timedelta(hours=4),
                last_activity_at=now,
                authenticated_at=now,
                user_agent="Operly Temporary Multi Workspace Stress Suite",
            )
        )
        await db.commit()
        return tenant.id, tenant.slug or tenant.id, session_secret, csrf_secret


async def _provision_workspace(base_url: str, plan: dict) -> dict:
    workspace_id, workspace_slug, session_secret, csrf_secret = await _bootstrap_workspace(plan["label"], plan["slug"])
    demo_token = secrets.token_urlsafe(32)
    demo_token_hash = hashlib.sha256(demo_token.encode("utf-8")).hexdigest()
    headers = {
        "Origin": base_url,
        "X-CSRF-Token": csrf_secret,
        "User-Agent": "Operly-Temp-Multi-Workspace/1",
    }
    cookies = {
        suite.PROD_SESSION_COOKIE: session_secret,
        suite.PROD_CSRF_COOKIE: csrf_secret,
    }
    records: list[dict] = []

    print(
        "TEMP_MULTI_WORKSPACE_STARTED",
        json.dumps({"label": plan["label"], "workspace_id": workspace_id, "workspace_slug": workspace_slug}, sort_keys=True),
        flush=True,
    )

    async with httpx.AsyncClient(base_url=base_url, headers=headers, cookies=cookies, timeout=150.0) as client:
        health = suite._json(await client.get("/api/health"))
        if not health.get("ok"):
            raise RuntimeError("Operly API is unhealthy")

        for plugin_id in plan["plugins"]:
            spec = SPEC_BY_ID[plugin_id]
            manifest, package_bytes = suite._package(spec)
            upload = suite._json(
                await client.post(
                    "/api/artifacts/upload",
                    files={"files": (f"{workspace_slug}-{plugin_id}.zip", package_bytes, "application/zip")},
                )
            )
            published = suite._json(
                await client.post(
                    "/api/plugin-platform/packages",
                    json={"manifest": manifest, "package_artifact_id": upload["artifact_ids"][0]},
                )
            )
            seed_state = {
                "records": spec["seed"],
                "updated_at": _utc_naive().isoformat(),
                "workspace_marker": workspace_slug,
            }
            installed = suite._json(
                await client.post(
                    "/api/plugin-platform/installations",
                    json={
                        "version_id": published["version_id"],
                        "granted_permissions": [],
                        "configuration": {
                            "temporary_demo": True,
                            "temporary_multi_workspace": True,
                            "remove_later": True,
                            "demo_token_hash": demo_token_hash,
                            "demo_name": spec["name"],
                            "demo_category": spec["category"],
                            "demo_description": spec["description"],
                            "demo_seed": seed_state,
                        },
                    },
                )
            )
            item = {
                "plugin_id": plugin_id,
                "name": spec["name"],
                "installation_id": installed["installation_id"],
                "capability_id": suite._capability_id(plugin_id),
            }
            records.append(item)
            print(
                "TEMP_MULTI_PUBLISHED",
                json.dumps({"workspace_id": workspace_id, **item}, sort_keys=True),
                flush=True,
            )

        await asyncio.gather(*(suite._wait_validation(client, item["installation_id"], timeout=600.0) for item in records))

        for item in records:
            accepted = suite._json(
                await client.post(
                    f"/api/plugin-platform/installations/{item['installation_id']}/runtime/reconcile",
                    json={},
                )
            )
            item["reconcile_job_id"] = accepted["job_id"]

        instances = await asyncio.gather(*(suite._wait_runtime(client, item["installation_id"], timeout=600.0) for item in records))
        for item, instance in zip(records, instances):
            item["runtime_provider"] = instance.get("provider")
            active = suite._json(
                await client.patch(
                    f"/api/plugin-platform/installations/{item['installation_id']}",
                    json={"status": "active", "enabled": True},
                )
            )
            if not active.get("enabled"):
                raise RuntimeError(f"Failed to activate {workspace_id}/{item['plugin_id']}")

        demo_headers = {"X-Operly-Demo-Token": demo_token, "Origin": base_url}
        apps = suite._json(
            await client.get(
                f"/api/public/plugin-demos/{workspace_id}/apps",
                headers=demo_headers,
                cookies={},
            )
        )
        if len(apps.get("apps", [])) != len(records):
            raise RuntimeError(f"Unexpected app count for {workspace_id}: {len(apps.get('apps', []))}")

        for item in records:
            spec = SPEC_BY_ID[item["plugin_id"]]
            state = {
                "records": spec["seed"],
                "updated_at": _utc_naive().isoformat(),
                "workspace_marker": workspace_slug,
            }
            suite._json(
                await client.put(
                    f"/api/public/plugin-demos/{workspace_id}/{item['plugin_id']}/state",
                    headers=demo_headers,
                    cookies={},
                    json={"state": state},
                )
            )
            hosted = await client.get(
                f"/api/public/plugins/{workspace_id}/{item['plugin_id']}/",
                cookies={},
            )
            if hosted.status_code != 200 or spec["name"] not in hosted.text:
                raise RuntimeError(f"Hosted UI failed for {workspace_id}/{item['plugin_id']}: HTTP {hosted.status_code}")
            item["hosted_url"] = f"{base_url}/api/public/plugins/{workspace_id}/{item['plugin_id']}/"

    return {
        "label": plan["label"],
        "workspace_id": workspace_id,
        "workspace_slug": workspace_slug,
        "demo_token": demo_token,
        "lab_url": f"{base_url}/temp-app-lab/{workspace_id}?token={demo_token}",
        "apps": records,
    }


async def _assert_isolation(base_url: str, workspaces: list[dict]) -> dict:
    retail = next(item for item in workspaces if item["label"] == "Retail Ops")
    service = next(item for item in workspaces if item["label"] == "Service Ops")
    plugin_id = "temp.inventory-planner"

    async with httpx.AsyncClient(base_url=base_url, timeout=120.0) as client:
        retail_headers = {"X-Operly-Demo-Token": retail["demo_token"]}
        service_headers = {"X-Operly-Demo-Token": service["demo_token"]}

        marker_state = {
            "records": [{"id": "ISO-ONLY", "sku": "ISO-ONLY", "item": "Retail-only isolation marker"}],
            "workspace_marker": "retail-only-isolation-check",
        }
        suite._json(
            await client.put(
                f"/api/public/plugin-demos/{retail['workspace_id']}/{plugin_id}/state",
                headers=retail_headers,
                json={"state": marker_state},
            )
        )
        service_state = suite._json(
            await client.get(
                f"/api/public/plugin-demos/{service['workspace_id']}/{plugin_id}/state",
                headers=service_headers,
            )
        )
        if service_state.get("state", {}).get("workspace_marker") == marker_state["workspace_marker"]:
            raise RuntimeError("Cross-Workspace storage isolation failed")

        cross = await client.get(
            f"/api/public/plugin-demos/{service['workspace_id']}/{plugin_id}/state",
            headers=retail_headers,
        )
        if cross.status_code < 400:
            raise RuntimeError("Cross-Workspace demo token was incorrectly accepted")

        reset = suite._json(
            await client.post(
                f"/api/public/plugin-demos/{retail['workspace_id']}/{plugin_id}/reset",
                headers=retail_headers,
                json={},
            )
        )
        if not isinstance(reset, dict):
            raise RuntimeError("Failed to reset Retail isolation marker")

    result = {
        "same_plugin_id": plugin_id,
        "retail_workspace": retail["workspace_id"],
        "service_workspace": service["workspace_id"],
        "cross_token_status": cross.status_code,
        "storage_isolated": True,
    }
    print("TEMP_MULTI_ISOLATION_PASS", json.dumps(result, sort_keys=True), flush=True)
    return result


async def _execute_all(base_url: str, workspaces: list[dict]) -> list[dict]:
    jobs: list[tuple[dict, dict]] = []
    for workspace in workspaces:
        for app in workspace["apps"]:
            jobs.append((workspace, app))

    async def run_one(workspace: dict, app: dict) -> dict:
        async with httpx.AsyncClient(base_url=base_url, timeout=180.0) as client:
            response = await client.post(
                f"/api/public/plugin-demos/{workspace['workspace_id']}/{app['plugin_id']}/execute",
                headers={"X-Operly-Demo-Token": workspace["demo_token"]},
                json={"action": "analyze"},
            )
            payload = suite._json(response)
            if not payload.get("result", {}).get("summary"):
                raise RuntimeError(f"Missing analysis summary for {workspace['workspace_id']}/{app['plugin_id']}")
            result = {
                "workspace_id": workspace["workspace_id"],
                "workspace_label": workspace["label"],
                "plugin_id": app["plugin_id"],
                "run_id": payload.get("run_id"),
                "status": "PASS",
            }
            print("TEMP_MULTI_EXECUTION_PASS", json.dumps(result, sort_keys=True), flush=True)
            return result

    return await asyncio.gather(*(run_one(workspace, app) for workspace, app in jobs))


async def main() -> None:
    await suite.init_db()
    base_url = (os.getenv("PUBLIC_BASE_URL") or "https://operly.dragonzpyder.xyz").strip().rstrip("/")

    workspaces = await asyncio.gather(*(_provision_workspace(base_url, plan) for plan in WORKSPACE_PLANS))
    isolation = await _assert_isolation(base_url, list(workspaces))
    executions = await _execute_all(base_url, list(workspaces))

    safe_workspaces = []
    for workspace in workspaces:
        safe_workspaces.append({
            "label": workspace["label"],
            "workspace_id": workspace["workspace_id"],
            "workspace_slug": workspace["workspace_slug"],
            "lab_url": workspace["lab_url"],
            "apps": workspace["apps"],
        })

    result = {
        "status": "PASS",
        "workspace_count": len(workspaces),
        "installation_count": sum(len(item["apps"]) for item in workspaces),
        "simultaneous_execution_count": len(executions),
        "isolation": isolation,
        "workspaces": safe_workspaces,
    }
    print("TEMP_MULTI_WORKSPACE_RESULT", json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
