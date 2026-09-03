from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import zipfile
from io import BytesIO
from typing import Any

import httpx

from apps.api.auth_cookies import PROD_CSRF_COOKIE, PROD_SESSION_COOKIE
from packages.plugins.temp_app_suite import _bootstrap_identity, _json, _wait_runtime, _wait_validation
from packages.plugins.temp_vibe_site_builder_assets import APP_CSS, APP_JS, DEFAULT_STATE
from packages.plugins.temp_vibe_site_builder_runtime import RUNTIME_PY

PLUGIN_ID = "temp.vibe-site-builder"
PLUGIN_NAME = "Vibe Site Builder"

def manifest() -> dict[str, Any]:
    return {
        "schema_version":"operly.plugin/v1","plugin_id":PLUGIN_ID,"version":"1.0.0","display_name":PLUGIN_NAME,
        "description":"A temporary vibe-coding site builder that directly exercises Operly Computer file, Python, shell and artifact tools inside its isolated Sandbox VM.",
        "execution_mode":"sandbox_job",
        "capabilities":[{"id":f"{PLUGIN_ID}.build","display_name":"Build site with Computer","description":"Generate, modify, validate, inspect and package an HTML/CSS/JS site using Operly Computer tools inside a fresh isolated Sandbox.","input_schema":{"type":"object","properties":{"action":{"type":"string"},"state":{"type":"object","additionalProperties":True}},"required":["action","state"],"additionalProperties":False},"output_schema":{"type":"object","properties":{"summary":{"type":"string"},"files":{"type":"object","additionalProperties":{"type":"string"}},"preview_html":{"type":"string"},"trace":{"type":"array","items":{"type":"object","additionalProperties":True}},"report":{"type":"object","additionalProperties":True},"bundle_base64":{"type":"string"},"bundle_sha256":{"type":"string"},"built_at":{"type":"string"}},"required":["summary","files","preview_html","trace","report","bundle_base64","bundle_sha256","built_at"],"additionalProperties":False},"permissions":[],"risk":"read_only","approval_required":False,"reversible":False,"aliases":["vibe site builder","html generator","website generator"],"emits":[],"tags":["temporary","computer-tools","vibe-coding","site-builder"]}],
        "permissions":[],"configuration_schema":{"type":"object","properties":{"temporary_demo":{"type":"boolean"},"demo_token_hash":{"type":"string"},"demo_name":{"type":"string"},"demo_category":{"type":"string"},"demo_description":{"type":"string"},"demo_seed":{"type":"object","additionalProperties":True}},"additionalProperties":False},
        "runtime":{"profile":"sandbox-job","kind":"job","network":{"mode":"off","allowed_hosts":[]},"resources":{"cpu_millicores":2000,"memory_mb":3072,"disk_mb":3072,"max_runtime_seconds":600,"max_concurrency":2}},
        "storage":[{"name":"app","kind":"document","quota_bytes":4*1024*1024}],"credentials":[],"produces_events":[],"consumes_events":[],"requested_bindings":[],
        "ui":[{"contribution_type":"navigation","id":f"{PLUGIN_ID}.home","title":PLUGIN_NAME,"configuration":{"hosted_entry":"index.html","temporary":True}}],
        "metadata":{"source":"operly-temp-functional-app-suite","temporary":True,"remove_later":True,"app_kind":"computer-site-builder","category":"Developer Tools","hosted_entry":"index.html","computer_tools_test":True},
    }

def package_bytes() -> bytes:
    buf=BytesIO()
    with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("operly.plugin.json",json.dumps(manifest(),separators=(",",":"),sort_keys=True))
        archive.writestr("operly_runtime.py",RUNTIME_PY)
        archive.writestr("index.html",'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Vibe Site Builder · Operly</title><link rel="stylesheet" href="assets/app.css"></head><body><div id="app"></div><script src="assets/app.js"></script></body></html>')
        archive.writestr("assets/app.js",APP_JS)
        archive.writestr("assets/app.css",APP_CSS)
        archive.writestr("seed.json",json.dumps(DEFAULT_STATE,indent=2))
        archive.writestr("TEMPORARY.md","Temporary Operly Computer-tools plugin test. Remove later.\n")
    return buf.getvalue()

async def main() -> None:
    base_url=(os.getenv("PUBLIC_BASE_URL") or "https://operly.dragonzpyder.xyz").strip().rstrip("/")
    workspace_id,workspace_slug,session_secret,csrf_secret=await _bootstrap_identity()
    token=secrets.token_urlsafe(32)
    token_hash=hashlib.sha256(token.encode()).hexdigest()
    print("TEMP_VIBE_BUILDER_WORKSPACE",workspace_id,workspace_slug,flush=True)
    headers={"Origin":base_url,"X-CSRF-Token":csrf_secret,"User-Agent":"Operly-Temp-Vibe-Builder/1"}
    cookies={PROD_SESSION_COOKIE:session_secret,PROD_CSRF_COOKIE:csrf_secret}
    async with httpx.AsyncClient(base_url=base_url,headers=headers,cookies=cookies,timeout=180.0) as client:
        if not _json(await client.get("/api/health")).get("ok"):
            raise RuntimeError("Operly API is unhealthy")
        upload=_json(await client.post("/api/artifacts/upload",files={"files":(f"{PLUGIN_ID}.zip",package_bytes(),"application/zip")}))
        published=_json(await client.post("/api/plugin-platform/packages",json={"manifest":manifest(),"package_artifact_id":upload["artifact_ids"][0]}))
        installed=_json(await client.post("/api/plugin-platform/installations",json={"version_id":published["version_id"],"granted_permissions":[],"configuration":{"temporary_demo":True,"demo_token_hash":token_hash,"demo_name":PLUGIN_NAME,"demo_category":"Developer Tools","demo_description":"Generate and package real HTML/CSS/JS projects using Operly Computer tools inside an isolated Sandbox.","demo_seed":DEFAULT_STATE}}))
        installation_id=installed["installation_id"]
        await _wait_validation(client,installation_id,timeout=600)
        accepted=_json(await client.post(f"/api/plugin-platform/installations/{installation_id}/runtime/reconcile",json={}))
        instance=await _wait_runtime(client,installation_id,timeout=600)
        active=_json(await client.patch(f"/api/plugin-platform/installations/{installation_id}",json={"status":"active","enabled":True}))
        if not active.get("enabled"):
            raise RuntimeError("Failed to activate Vibe Site Builder")
        demo_headers={"X-Operly-Demo-Token":token,"Origin":base_url}
        _json(await client.put(f"/api/public/plugin-demos/{workspace_id}/{PLUGIN_ID}/state",headers=demo_headers,json={"state":DEFAULT_STATE}))
        hosted=await client.get(f"/api/public/plugins/{workspace_id}/{PLUGIN_ID}/")
        asset=await client.get(f"/api/public/plugins/{workspace_id}/{PLUGIN_ID}/assets/app.js")
        if hosted.status_code!=200 or "Vibe Site Builder" not in hosted.text or asset.status_code!=200 or "Computer trace" not in asset.text:
            raise RuntimeError("Hosted Vibe Site Builder UI failed")
        execution=_json(await client.post(f"/api/public/plugin-demos/{workspace_id}/{PLUGIN_ID}/execute",headers=demo_headers,json={"action":"analyze"}))
        result=execution.get("result") or {}
        required={"environment.info","files.mkdir","files.write","python.exec","terminal.exec","files.read","files.list","artifact.export"}
        seen={str(x.get("tool")) for x in result.get("trace") or [] if isinstance(x,dict)}
        missing=sorted(required-seen)
        if missing:
            raise RuntimeError(f"Computer tool trace missing: {missing}")
        if not result.get("preview_html") or not result.get("bundle_base64") or "Northstar Studio" not in result.get("preview_html","") or not result.get("bundle_sha256"):
            raise RuntimeError("Builder output verification failed")
        print("TEMP_VIBE_BUILDER_RESULT",json.dumps({
            "status":"PASS","workspace_id":workspace_id,"workspace_slug":workspace_slug,"plugin_id":PLUGIN_ID,
            "installation_id":installation_id,"reconcile_job_id":accepted.get("job_id"),"runtime_provider":instance.get("provider"),
            "lab_url":f"{base_url}/temp-app-lab/{workspace_id}?token={token}","hosted_url":f"{base_url}/api/public/plugins/{workspace_id}/{PLUGIN_ID}/",
            "tool_count":len(result.get("trace") or []),"tools":sorted(seen),"bundle_sha256":result.get("bundle_sha256"),"summary":result.get("summary")
        },sort_keys=True),flush=True)

if __name__=="__main__":
    asyncio.run(main())
