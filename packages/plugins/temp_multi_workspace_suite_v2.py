from __future__ import annotations

import asyncio
import json
import os

import httpx

from packages.plugins import temp_app_suite as suite
from packages.plugins import temp_multi_workspace_suite as multi


async def _execute_all_collect(base_url: str, workspaces: list[dict]) -> list[dict]:
    jobs: list[tuple[dict, dict]] = [
        (workspace, app)
        for workspace in workspaces
        for app in workspace["apps"]
    ]

    async def run_one(workspace: dict, app: dict) -> dict:
        result = {
            "workspace_id": workspace["workspace_id"],
            "workspace_label": workspace["label"],
            "plugin_id": app["plugin_id"],
        }
        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=240.0) as client:
                response = await client.post(
                    f"/api/public/plugin-demos/{workspace['workspace_id']}/{app['plugin_id']}/execute",
                    headers={"X-Operly-Demo-Token": workspace["demo_token"]},
                    json={"action": "analyze"},
                )
                result["http_status"] = response.status_code
                if response.status_code >= 400:
                    result["status"] = "FAIL"
                    result["error"] = response.text[:500]
                    print("TEMP_MULTI_EXECUTION_FAIL", json.dumps(result, sort_keys=True), flush=True)
                    return result
                payload = suite._json(response)
                if not payload.get("result", {}).get("summary"):
                    result["status"] = "FAIL"
                    result["error"] = "missing analysis summary"
                    print("TEMP_MULTI_EXECUTION_FAIL", json.dumps(result, sort_keys=True), flush=True)
                    return result
                result["run_id"] = payload.get("run_id")
                result["status"] = "PASS"
                print("TEMP_MULTI_EXECUTION_PASS", json.dumps(result, sort_keys=True), flush=True)
                return result
        except Exception as error:
            result["status"] = "FAIL"
            result["error"] = f"{type(error).__name__}: {error}"[:500]
            print("TEMP_MULTI_EXECUTION_FAIL", json.dumps(result, sort_keys=True), flush=True)
            return result

    # All requests are started together. Operly's real sandbox-job admission gate,
    # not this harness, is responsible for applying bounded backpressure.
    return list(await asyncio.gather(*(run_one(workspace, app) for workspace, app in jobs)))


async def main() -> None:
    await suite.init_db()
    base_url = (os.getenv("PUBLIC_BASE_URL") or "https://operly.dragonzpyder.xyz").strip().rstrip("/")

    workspaces = list(
        await asyncio.gather(*(multi._provision_workspace(base_url, plan) for plan in multi.WORKSPACE_PLANS))
    )
    isolation = await multi._assert_isolation(base_url, workspaces)
    executions = await _execute_all_collect(base_url, workspaces)

    failures = [item for item in executions if item.get("status") != "PASS"]
    safe_workspaces = [
        {
            "label": workspace["label"],
            "workspace_id": workspace["workspace_id"],
            "workspace_slug": workspace["workspace_slug"],
            "lab_url": workspace["lab_url"],
            "apps": workspace["apps"],
        }
        for workspace in workspaces
    ]
    result = {
        "status": "PASS" if not failures else "FAIL",
        "workspace_count": len(workspaces),
        "installation_count": sum(len(item["apps"]) for item in workspaces),
        "simultaneous_request_count": len(executions),
        "execution_pass_count": len(executions) - len(failures),
        "execution_fail_count": len(failures),
        "isolation": isolation,
        "workspaces": safe_workspaces,
        "failures": failures,
    }
    print("TEMP_MULTI_WORKSPACE_RESULT", json.dumps(result, sort_keys=True), flush=True)
    if failures:
        raise RuntimeError(f"{len(failures)} of {len(executions)} simultaneous executions failed")


if __name__ == "__main__":
    asyncio.run(main())
